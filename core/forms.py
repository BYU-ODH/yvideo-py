import logging
import re

from django import forms
from django.core.exceptions import ValidationError

from .api import Api
from .model_utils import update_user_enrollment
from .models import Clip
from .models import Content
from .models import ImportantWord
from .models import Playlist
from .models import ResourceIntakeRequest
from .models import Subtitle
from .models import User
from .utils import hms2seconds

logger = logging.getLogger(__name__)

BYU_ID_PATTERN = re.compile(r"^\d{9}$")
NETID_PATTERN = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9]{2,8}$")


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


class ImportantWordForm(forms.ModelForm):
    class Meta:
        model = ImportantWord
        fields = ["word", "translation"]

    word = forms.CharField(required=True)
    translation = forms.CharField(required=True)


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
        except Exception:
            logger.exception(
                "Failed to create user from BYU API for byu_id=%s during admin "
                "add-user lookup.",
                byu_id,
            )
            raise ValidationError(
                "Couldn't reach BYU's directory to create this user. Try again "
                "in a moment."
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

        api = Api()
        try:
            student_summary = api.get_student_summary(net_id=netid)
        except Exception:
            logger.exception(
                "Failed to look up NetID %s via the student summary API "
                "during admin add-user lookup.",
                netid,
            )
            raise ValidationError(
                "Couldn't reach BYU's directory to look up that NetID. Try "
                "again in a moment."
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
