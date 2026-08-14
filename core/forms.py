import logging
import re

from django import forms
from django.core.exceptions import ValidationError
import requests

from .api import Api
from .model_utils import update_user_enrollment
from .models import Clip
from .models import Content
from .models import Playlist
from .models import ResourceIntakeRequest
from .models import Subtitle
from .models import User
from .utils import hms2seconds

logger = logging.getLogger(__name__)

BYU_ID_PATTERN = re.compile(r"^\d{9}$")
NETID_PATTERN = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9]{2,8}$")

# A missing or blank BYU API setting fails as one of these rather than as a network error.
# secret_settings.py is gitignored, so a deployment whose copy predates a key added to
# secret_settings_template.py raises AttributeError on first use, and the template's own
# empty-string URLs make requests raise MissingSchema.
MISCONFIGURATION_ERRORS = (
    AttributeError,
    requests.exceptions.MissingSchema,
    requests.exceptions.InvalidSchema,
    requests.exceptions.InvalidURL,
)

MISCONFIGURED_DIRECTORY_MESSAGE = (
    "Y-Video isn't set up to reach BYU's directory. That's a server configuration "
    "problem rather than something retrying will fix -- ask an administrator to check "
    "the BYU API settings."
)


def directory_lookup_error(exception, transient_message):
    """The ValidationError to show for a failed BYU API call.

    Misconfiguration is not an outage, and the two need different messages: telling
    someone to "try again in a moment" when a setting is missing sends them into a loop
    that cannot end. That is not hypothetical -- API_NET_ID_IAM_URL was added to
    secret_settings_template.py well after the deployments that use it, and every install
    that missed it reported the omission as a temporary BYU problem.
    """
    if isinstance(exception, MISCONFIGURATION_ERRORS):
        return ValidationError(MISCONFIGURED_DIRECTORY_MESSAGE)
    return ValidationError(transient_message)


class PlaylistForm(forms.ModelForm):
    name = forms.CharField(
        widget=forms.TextInput(attrs={"placeholder": "Playlist Name"})
    )

    def clean_name(self):
        name = self.cleaned_data["name"]

        if Playlist.objects.filter(owner=self.initial.get("user"), name=name).exists():
            raise ValidationError("You already have a playlist with this name.")

        return name

    class Meta:
        model = Playlist
        fields = ("name",)


class PlaylistSettingsForm(forms.ModelForm):
    class Meta:
        model = Playlist
        fields = ["name", "published", "archived"]


class UpdateContentForm(forms.ModelForm):
    confirm_guidelines = forms.BooleanField(label="guidelines", required=True)

    class Meta:
        model = Content
        fields = [
            "id",
            "title",
            "description",
            "allow_definitions",
            "allow_notes",
            "allow_captions",
            "allow_fast_playback",
            "clips_only",
            "published",
        ]

    id = forms.CharField(widget=forms.HiddenInput)


class ClipForm(forms.ModelForm):
    class Meta:
        model = Clip
        fields = ["name", "start_time", "end_time"]

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")

        if start_time and end_time:
            start_seconds = hms2seconds(start_time)
            end_seconds = hms2seconds(end_time)

            if start_seconds >= end_seconds:
                raise forms.ValidationError("End time must be after start time.")

        return cleaned_data


class SubtitleForm(forms.ModelForm):
    class Meta:
        model = Subtitle
        fields = [
            "resource",
            "owner",
            "language",
            "name",
            "subtitles_file",
            "is_original",
        ]

    resource = forms.CharField(widget=forms.HiddenInput)
    owner = forms.CharField(widget=forms.HiddenInput)


class ResourceIntakeRequestForm(forms.ModelForm):
    acknowledged_compliance = forms.BooleanField(required=True)
    acknowledged_fair_use_limitation = forms.BooleanField(required=True)

    class Meta:
        model = ResourceIntakeRequest
        exclude = []
        widgets = {
            "date_needed": forms.DateInput(attrs={"type": "date"}),
        }


class AddUserLookupForm(forms.Form):
    identifier = forms.CharField(
        label="BYU ID or NetID",
        help_text=(
            "Enter a 9-digit BYU ID or a NetID — their name, NetID/BYU ID, and "
            "permissions will be filled in automatically from BYU's directory. "
            "A NetID can only create a new user if they have a BYU student "
            "record; otherwise, use their 9-digit BYU ID, or have them log in "
            "to Y-Video themselves."
        ),
        widget=forms.TextInput(attrs={"autofocus": True}),
    )

    def clean_identifier(self):
        value = self.cleaned_data["identifier"].strip()
        if BYU_ID_PATTERN.match(value):
            self.resolved_user, self.created = self._resolve_byu_id(value)
        elif NETID_PATTERN.match(value):
            self.resolved_user, self.created = self._resolve_netid(value)
        else:
            raise ValidationError("Enter a 9-digit BYU ID or a valid NetID.")
        return value

    def _resolve_byu_id(self, byu_id):
        existing = User.objects.filter(username=byu_id).first()
        if existing:
            return existing, False

        # Reuse the same API-driven lookup used to provision users at SSO login.
        from yvideo.odhOIDCAuthenticationBackend import OIDCUserAuth

        try:
            created_user = OIDCUserAuth().create_user({"byu_id": byu_id})
            if created_user is not None:
                enrollment_result = update_user_enrollment(created_user)
        except Exception as exception:
            # Not "admin": this form is also the Manage People lookup, so naming one
            # caller would misdirect whoever reads the log.
            logger.exception(
                "Failed to create user from BYU API for byu_id=%s during an "
                "add-user lookup.",
                byu_id,
            )
            raise directory_lookup_error(
                exception,
                "Couldn't reach BYU's directory to create this user. Try again "
                "in a moment.",
            )

        if created_user is None:
            raise ValidationError(
                "BYU's directory has no record that qualifies this BYU ID for "
                "an account (not currently faculty, ODH staff, or an active "
                "student). Double-check the number, or confirm eligibility "
                "with BYU IT."
            )

        if not (
            enrollment_result["is_current_sem_updated"]
            and enrollment_result["is_next_sem_updated"]
        ):
            self.enrollment_warning = enrollment_result["result_message"]

        return created_user, True

    def _resolve_netid(self, netid):
        existing = User.objects.filter(netid__iexact=netid).first()
        if existing:
            return existing, False

        try:
            # Api() itself talks to BYU -- it mints an auth token in its constructor --
            # so it has to be inside the try. Left outside, a blank API_AUTH_TOKEN_URL
            # escaped as a 500 instead of reaching the person as a message.
            student_summary = Api().get_student_summary(net_id=netid)
        except Exception as exception:
            logger.exception(
                "Failed to look up NetID %s via the student summary API during an "
                "add-user lookup.",
                netid,
            )
            raise directory_lookup_error(
                exception,
                "Couldn't reach BYU's directory to look up that NetID. Try "
                "again in a moment.",
            )

        if student_summary is None:
            raise ValidationError(
                "No BYU student record was found for that NetID. This person "
                "may need to log in to Y-Video themselves to create their "
                "account, or you can add them directly with their 9-digit "
                "BYU ID."
            )

        # A NetID with a student record may still belong to someone who is now
        # faculty/staff — let create_user's own worker-vs-student check (inside
        # _resolve_byu_id) decide their current role rather than assuming.
        return self._resolve_byu_id(student_summary["byu_id"])
