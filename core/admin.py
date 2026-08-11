from difflib import SequenceMatcher
import logging
import os
import re

from django import forms
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html
from reversion.admin import VersionAdmin

from .forms import AddUserLookupForm
from .models import AnnotationSet
from .models import BlankAnnotation
from .models import BlurAnnotation
from .models import Clip
from .models import CommentAnnotation
from .models import Content
from .models import Course
from .models import Email
from .models import ImportantWord
from .models import Language
from .models import MuteAnnotation
from .models import PauseAnnotation
from .models import Playlist
from .models import PlaylistUserAccess
from .models import Resource
from .models import ResourceAccess
from .models import ResourceFile
from .models import ResourceFileKey
from .models import ResourceIntakeRequest
from .models import SkipAnnotation
from .models import Subtitle
from .models import Track
from .models import User
from .models import UserCourses
from .utils import convert_srt_content_to_vtt

logger = logging.getLogger(__name__)

IMDB_ID_PATTERN = re.compile(r"\btt\d{7,}\b", re.IGNORECASE)
FUZZY_RESOURCE_MATCH_THRESHOLD = 80
MAX_FUZZY_RESOURCE_MATCHES = 5


def _extract_imdb_id(value):
    match = IMDB_ID_PATTERN.search(value or "")
    return match.group(0).lower() if match else ""


def _normalize_match_value(value):
    return "".join(
        character for character in (value or "").casefold() if character.isalnum()
    )


def _resource_match_candidates(intake_request):
    """Return likely existing Resources, with exact IMDb matches ranked first."""
    if not intake_request:
        return []

    resources = list(Resource.objects.all())
    requested_imdb_id = _extract_imdb_id(intake_request.imdb_link)
    normalized_title = _normalize_match_value(intake_request.resource_title)
    candidates_by_id = {}

    for resource in resources:
        if (
            requested_imdb_id
            and _extract_imdb_id(resource.imdb_id) == requested_imdb_id
        ):
            candidates_by_id[resource.pk] = {
                "resource": resource,
                "match_type": "imdb",
                "score": 100,
                "description": f"IMDb ID {requested_imdb_id}",
            }

    fuzzy_matches = []
    if normalized_title:
        for resource in resources:
            normalized_resource_name = _normalize_match_value(resource.name)
            score = round(
                SequenceMatcher(
                    None, normalized_title, normalized_resource_name
                ).ratio()
                * 100
            )
            if score >= FUZZY_RESOURCE_MATCH_THRESHOLD:
                fuzzy_matches.append((score, resource))

    fuzzy_matches.sort(key=lambda match: (-match[0], match[1].name.casefold()))
    for score, resource in fuzzy_matches[:MAX_FUZZY_RESOURCE_MATCHES]:
        if resource.pk in candidates_by_id:
            continue
        candidates_by_id[resource.pk] = {
            "resource": resource,
            "match_type": "name",
            "score": score,
            "description": f"similar title ({score}%)",
        }

    if (
        intake_request.generated_resource_id
        and intake_request.generated_resource_id not in candidates_by_id
    ):
        resource = intake_request.generated_resource
        candidates_by_id[resource.pk] = {
            "resource": resource,
            "match_type": "associated",
            "score": 100,
            "description": "currently associated with this request",
        }

    return sorted(
        candidates_by_id.values(),
        key=lambda candidate: (
            {"associated": 0, "imdb": 1, "name": 2}[candidate["match_type"]],
            -candidate["score"],
            candidate["resource"].name.casefold(),
        ),
    )


def _language_matches_request(language, requested_language):
    if not requested_language or not requested_language.strip():
        return True
    if not language:
        return False

    requested_values = {
        _normalize_match_value(part)
        for part in re.split(r"[/,;()]+", requested_language)
        if _normalize_match_value(part)
    }
    language_values = {
        _normalize_match_value(language.language),
        _normalize_match_value(language.bcp47),
    }
    return bool(requested_values & language_values)


def _resource_file_matches_request(resource_file, intake_request):
    return _resource_file_matches_languages(
        resource_file,
        intake_request.audio_language,
        intake_request.subtitle_language,
    )


def _resource_file_matches_languages(resource_file, audio_language, subtitle_language):
    return _language_matches_request(
        resource_file.audio_language, audio_language
    ) and _language_matches_request(
        resource_file.burned_in_subtitles_language,
        subtitle_language,
    )


def _resolve_requested_language(value):
    normalized_value = _normalize_match_value(value)
    if not normalized_value:
        return None
    return next(
        (
            language
            for language in Language.objects.all()
            if _language_matches_request(language, value)
        ),
        None,
    )


class ResourceIntakeRequestAdminForm(forms.ModelForm):
    CREATE_RESOURCE = "create-resource"
    UPLOAD_FILE = "upload-file"

    resource_to_use = forms.ChoiceField(
        choices=(),
        widget=forms.RadioSelect,
        label="Resource to use",
        help_text=(
            "Choose a likely existing Resource or create a new one from the "
            "request information above."
        ),
    )
    new_resource_imdb_id = forms.CharField(
        required=False,
        label="IMDb id",
        help_text=(
            "Required when creating a Resource that is listed in IMDb, e.g. "
            "tt2278388. Find it in the URL of the title's IMDb page: "
            "imdb.com/title/tt2278388/."
        ),
    )
    new_resource_not_in_imdb = forms.BooleanField(
        required=False,
        label="Resource is not in IMDb",
        help_text="An internal BYU Resource ID will be generated.",
    )
    file_to_use = forms.ChoiceField(
        choices=(),
        widget=forms.RadioSelect,
        label="File to use",
        help_text=(
            "Choose a ResourceFile that matches the requested languages, or "
            "upload a new file."
        ),
    )
    new_resource_file = forms.FileField(
        required=False,
        label="File upload",
        help_text='Required when "Upload new file" is selected.',
    )
    new_resource_file_version = forms.CharField(
        required=False,
        max_length=100,
        label="Uploaded file version",
        help_text="Optional; leave blank if the file has no distinct version.",
    )
    new_resource_file_audio_language = forms.ModelChoiceField(
        queryset=Language.objects.all(),
        required=False,
        label="Uploaded file audio language",
        help_text="Optional; leave blank for media without spoken audio.",
    )
    new_resource_file_subtitle_language = forms.ModelChoiceField(
        queryset=Language.objects.all(),
        required=False,
        label="Uploaded file burned-in subtitle language",
        help_text="Optional; leave blank for media without burned-in subtitles.",
    )
    new_resource_file_full_video = forms.BooleanField(
        required=False,
        initial=True,
        label="Uploaded file contains the full video",
        help_text="Clear this checkbox if the uploaded file is only a portion.",
    )
    new_resource_file_barcode = forms.CharField(
        required=False,
        max_length=13,
        label="Uploaded file barcode",
        help_text="Enter the 12- or 13-digit EAN/UPC barcode.",
    )
    new_resource_file_has_no_barcode = forms.BooleanField(
        required=False,
        label="Uploaded file has no barcode",
        help_text="An internal BYU barcode will be generated.",
    )
    user_has_shown_proof_of_ownership = forms.BooleanField(
        required=False,
        label="User has shown proof of ownership",
        help_text="Required before this intake request can be approved.",
    )

    class Meta:
        model = ResourceIntakeRequest
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        candidates = _resource_match_candidates(self.instance)

        self.candidate_resources = {
            str(candidate["resource"].pk): candidate["resource"]
            for candidate in candidates
        }
        resource_choices = [
            (
                self.CREATE_RESOURCE,
                "Create from scratch",
            )
        ]
        for candidate in candidates:
            resource = candidate["resource"]
            imdb_id = _extract_imdb_id(resource.imdb_id)
            if imdb_id:
                imdb_url = f"https://www.imdb.com/title/{imdb_id}/"
                imdb_display = format_html(
                    '<a href="{}" target="_blank" rel="noopener">IMDb {}</a>',
                    imdb_url,
                    imdb_id,
                )
            else:
                imdb_display = format_html(
                    '<span class="intake-missing-imdb">{}</span>',
                    "(no IMDb id)",
                )
            resource_choices.append(
                (
                    str(resource.pk),
                    format_html(
                        "{} — {} — {}",
                        resource.name,
                        imdb_display,
                        candidate["description"],
                    ),
                )
            )
        self.fields["resource_to_use"].choices = resource_choices

        self.candidate_files = {}
        file_choices = [(self.UPLOAD_FILE, "Upload new file")]
        for candidate in candidates:
            resource = candidate["resource"]
            for resource_file in resource.resource_files.select_related(
                "audio_language", "burned_in_subtitles_language"
            ).order_by("version", "pk"):
                self.candidate_files[str(resource_file.pk)] = resource_file
                language_status = _resource_file_matches_request(
                    resource_file, self.instance
                )
                file_choices.append(
                    (
                        str(resource_file.pk),
                        self._resource_file_label(
                            resource, resource_file, language_status
                        ),
                    )
                )
        self.fields["file_to_use"].choices = file_choices

        if self.instance.generated_resource_id:
            self.fields["resource_to_use"].initial = str(
                self.instance.generated_resource_id
            )
        elif len(candidates) == 1:
            self.fields["resource_to_use"].initial = str(candidates[0]["resource"].pk)
        elif not candidates:
            self.fields["resource_to_use"].initial = self.CREATE_RESOURCE

        matching_files = [
            resource_file
            for resource_file in self.candidate_files.values()
            if _resource_file_matches_request(resource_file, self.instance)
        ]
        if len(matching_files) == 1:
            self.fields["file_to_use"].initial = str(matching_files[0].pk)
        elif not matching_files:
            self.fields["file_to_use"].initial = self.UPLOAD_FILE

        self.fields[
            "new_resource_file_audio_language"
        ].initial = _resolve_requested_language(self.instance.audio_language)
        self.fields[
            "new_resource_file_subtitle_language"
        ].initial = _resolve_requested_language(self.instance.subtitle_language)
        self.fields["new_resource_imdb_id"].initial = _extract_imdb_id(
            self.instance.imdb_link
        )

        if self.is_bound:
            create_selected = self.data.get("resource_to_use") == self.CREATE_RESOURCE
            resource_not_in_imdb = bool(self.data.get("new_resource_not_in_imdb"))
            upload_selected = (
                create_selected or self.data.get("file_to_use") == self.UPLOAD_FILE
            )
            file_has_no_barcode = bool(
                self.data.get("new_resource_file_has_no_barcode")
            )
            checked_out_from_library = bool(
                self.data.get("checked_out_from_hbll")
            ) or bool(self.data.get("checked_out_from_other_byu_library"))
        else:
            create_selected = (
                self.fields["resource_to_use"].initial == self.CREATE_RESOURCE
            )
            resource_not_in_imdb = False
            upload_selected = self.fields["file_to_use"].initial == self.UPLOAD_FILE
            file_has_no_barcode = False
            checked_out_from_library = (
                self.instance.checked_out_from_hbll
                or self.instance.checked_out_from_other_byu_library
            )

        self.fields["new_resource_imdb_id"].required = (
            create_selected and not resource_not_in_imdb
        )
        self.fields["new_resource_file"].required = upload_selected
        self.fields["new_resource_file_barcode"].required = (
            upload_selected and not file_has_no_barcode
        )
        self.fields["byu_call_number"].required = checked_out_from_library

    @staticmethod
    def _resource_file_label(resource, resource_file, language_status):
        file_name = os.path.basename(resource_file.file.name)
        scope = "full work" if resource_file.full_video else "portion"
        audio = resource_file.audio_language or "not specified"
        subtitles = resource_file.burned_in_subtitles_language or "none"
        barcode = resource_file.barcode or "not assigned"
        status_class = (
            "intake-file-status--match"
            if language_status
            else "intake-file-status--mismatch"
        )
        status_text = (
            "Matches requested languages"
            if language_status
            else "Does not match requested languages"
        )
        return format_html(
            '<strong class="intake-file-title">{} — {}</strong>'
            '<span class="intake-file-meta"><strong>File:</strong> {}</span>'
            '<span class="intake-file-meta"><strong>Audio:</strong> {}</span>'
            '<span class="intake-file-meta"><strong>Burned-in subtitles:</strong> '
            "{}</span>"
            '<span class="intake-file-meta"><strong>Scope:</strong> {}</span>'
            '<span class="intake-file-meta"><strong>Barcode:</strong> {}</span>'
            '<span class="intake-file-status {}">{}</span>',
            resource.name,
            resource_file.version,
            file_name,
            audio,
            subtitles,
            scope,
            barcode,
            status_class,
            status_text,
        )

    def clean(self):
        cleaned_data = super().clean()
        resource_choice = cleaned_data.get("resource_to_use")
        file_choice = cleaned_data.get("file_to_use")
        approving_request = "_approve_request" in self.data

        if resource_choice == self.CREATE_RESOURCE:
            selected_resource = None
            if file_choice != self.UPLOAD_FILE:
                file_choice = self.UPLOAD_FILE
                cleaned_data["file_to_use"] = self.UPLOAD_FILE
        else:
            selected_resource = self.candidate_resources.get(resource_choice)

        selected_file = None
        if file_choice and file_choice != self.UPLOAD_FILE:
            selected_file = self.candidate_files.get(file_choice)

        pending_resource_file = None
        if approving_request:
            owner = cleaned_data.get("owner")
            if not owner:
                self.add_error(
                    "resource_to_use",
                    "The request must have an owner before it can be approved.",
                )
            if not cleaned_data.get("user_has_shown_proof_of_ownership"):
                self.add_error(
                    "user_has_shown_proof_of_ownership",
                    "Confirm that the user has shown proof of ownership before "
                    "approving this request.",
                )

            if resource_choice == self.CREATE_RESOURCE:
                resource_title = cleaned_data.get("resource_title") or ""
                if Resource.objects.filter(name=resource_title).exists():
                    self.add_error(
                        "resource_to_use",
                        "A Resource with this exact name already exists. Select it or "
                        "change the request title before creating a new Resource.",
                    )
                if cleaned_data.get("new_resource_not_in_imdb"):
                    cleaned_data["resolved_new_resource_imdb_id"] = ""
                else:
                    submitted_imdb_id = (
                        cleaned_data.get("new_resource_imdb_id") or ""
                    ).strip()
                    imdb_id = _extract_imdb_id(submitted_imdb_id)
                    if imdb_id != submitted_imdb_id.casefold():
                        self.add_error(
                            "new_resource_imdb_id",
                            "Enter an IMDb title ID in the form tt1234567.",
                        )
                    elif Resource.objects.filter(imdb_id__iexact=imdb_id).exists():
                        self.add_error(
                            "new_resource_imdb_id",
                            "A Resource with this IMDb ID already exists. Select "
                            "that Resource instead.",
                        )
                    cleaned_data["resolved_new_resource_imdb_id"] = imdb_id
            elif not selected_resource and resource_choice:
                self.add_error(
                    "resource_to_use",
                    "Select one of the Resource candidates shown.",
                )

            if selected_file and (
                not selected_resource
                or selected_file.resource_id != selected_resource.pk
            ):
                self.add_error(
                    "file_to_use",
                    "The selected ResourceFile does not belong to the selected Resource.",
                )
            elif selected_file and not _resource_file_matches_languages(
                selected_file,
                cleaned_data.get("audio_language"),
                cleaned_data.get("subtitle_language"),
            ):
                self.add_error(
                    "file_to_use",
                    "The selected ResourceFile does not match the requested audio "
                    "and subtitle languages. Upload a new file instead.",
                )
            elif file_choice and file_choice != self.UPLOAD_FILE and not selected_file:
                self.add_error(
                    "file_to_use",
                    "Select one of the ResourceFile candidates shown.",
                )

            if file_choice == self.UPLOAD_FILE:
                pending_resource_file = self._clean_uploaded_resource_file(
                    cleaned_data, selected_resource
                )

        cleaned_data["selected_resource"] = selected_resource
        cleaned_data["selected_resource_file"] = selected_file
        cleaned_data["pending_resource_file"] = pending_resource_file
        return cleaned_data

    def _clean_uploaded_resource_file(self, cleaned_data, selected_resource):
        uploaded_file = cleaned_data.get("new_resource_file")
        if not uploaded_file:
            return None

        version = cleaned_data.get("new_resource_file_version")
        audio_language = cleaned_data.get("new_resource_file_audio_language")
        subtitle_language = cleaned_data.get("new_resource_file_subtitle_language")
        full_video = cleaned_data.get("new_resource_file_full_video")
        file_has_no_barcode = cleaned_data.get("new_resource_file_has_no_barcode")
        barcode = (
            None
            if file_has_no_barcode
            else cleaned_data.get("new_resource_file_barcode")
        )
        if not file_has_no_barcode and not barcode:
            return None
        if barcode and ResourceFile.objects.filter(barcode=barcode).exists():
            self.add_error(
                "new_resource_file_barcode",
                "A ResourceFile with this barcode already exists.",
            )
            return None

        if audio_language and not _language_matches_request(
            audio_language, cleaned_data.get("audio_language")
        ):
            self.add_error(
                "new_resource_file_audio_language",
                "Choose the audio language requested above.",
            )
        if subtitle_language and not _language_matches_request(
            subtitle_language, cleaned_data.get("subtitle_language")
        ):
            self.add_error(
                "new_resource_file_subtitle_language",
                "Choose the burned-in subtitle language requested above.",
            )

        resource = selected_resource or Resource(
            name=cleaned_data.get("resource_title") or "",
            media_type=Resource.MediaType.VIDEO,
            requester_username=(
                cleaned_data["owner"].username if cleaned_data.get("owner") else ""
            ),
        )
        resource_file = ResourceFile(
            resource=resource,
            file=uploaded_file,
            version=version,
            audio_language=audio_language,
            burned_in_subtitles_language=subtitle_language,
            full_video=full_video,
            barcode=barcode,
        )
        try:
            resource_file.full_clean(
                exclude=["resource"] if not selected_resource else None
            )
        except ValidationError as error:
            self._add_resource_file_validation_errors(error)
            return None
        return resource_file

    def _add_resource_file_validation_errors(self, error):
        field_map = {
            "file": "new_resource_file",
            "version": "new_resource_file_version",
            "audio_language": "new_resource_file_audio_language",
            "burned_in_subtitles_language": ("new_resource_file_subtitle_language"),
            "barcode": "new_resource_file_barcode",
        }
        if hasattr(error, "message_dict"):
            for field_name, field_errors in error.message_dict.items():
                form_field = field_map.get(field_name)
                for field_error in field_errors:
                    self.add_error(form_field, field_error)
        else:
            for field_error in error.messages:
                self.add_error(None, field_error)


@admin.register(User)
class UserAdmin(VersionAdmin):
    list_display = (
        "username",
        "netid",
        "first_name",
        "last_name",
        "email",
        "privilege_level",
        "group_names",
        "date_joined",
    )
    list_filter = ("groups", "privilege_level", "date_joined")
    search_fields = ("username", "netid", "first_name", "last_name")
    add_form_template = "admin/core/user/add_form.html"

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("groups")

    @admin.display(description="Groups")
    def group_names(self, user):
        return ", ".join(group.name for group in user.groups.all()) or "—"

    def add_view(self, request, form_url="", extra_context=None):
        # A new user's data (name, netid, permissions) comes entirely from BYU's
        # APIs, so instead of Django's generic model-field add form, admins just
        # supply a BYU ID (to create/populate a user) or a NetID (to find an
        # existing one).
        with self.create_revision(request):
            return self._add_view(request, form_url, extra_context)

    def _add_view(self, request, form_url="", extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied

        if request.method == "POST":
            form = AddUserLookupForm(request.POST)
            if form.is_valid():
                user = form.resolved_user
                if form.created:
                    self.log_addition(
                        request, user, "Added via BYU ID lookup in admin."
                    )
                    messages.success(
                        request,
                        f'The user "{user}" was created and populated from BYU\'s directory.',
                    )
                    if getattr(form, "enrollment_warning", None):
                        messages.warning(request, form.enrollment_warning)
                else:
                    messages.info(request, f'Found existing user "{user}".')
                return HttpResponseRedirect(
                    reverse("admin:core_user_change", args=(user.pk,))
                )
        else:
            form = AddUserLookupForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Add user",
            "opts": self.model._meta,
        }
        if extra_context:
            context.update(extra_context)
        context["form"] = form
        return render(request, self.add_form_template, context)


@admin.register(Resource)
class ResourceAdmin(VersionAdmin):
    list_display = (
        "name",
        "media_type",
        "requester_username",
        "copyrighted",
        "views",
        "created_at",
    )
    list_filter = ("media_type", "copyrighted", "physical_copy_exists", "created_at")
    search_fields = ("name",)

    class Media:
        js = ("js/admin_call_number.js",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Provide Resource Access to the request that wanted this Resource to be created
        # This only works if the user exists. We cannot build users based off of netid, so
        # without BYUID, there is no way to create a non-existant user if the requester is
        # not already in the system.
        requester_username = obj.requester_username
        try:
            user = User.objects.get(username=requester_username)
        except Exception:
            return
        ResourceAccess.objects.get_or_create(user=user, resource=obj)


@admin.register(ResourceIntakeRequest)
class ResourceIntakeRequestAdmin(VersionAdmin):
    form = ResourceIntakeRequestAdminForm
    change_form_template = "admin/core/resourceintakerequest/change_form.html"
    list_display = (
        "resource_title",
        "owner",
        "date_needed",
        "existing_matches_summary",
        "generated_resource",
        "created_at",
    )
    list_filter = ("checked_out_from_hbll", "checked_out_from_other_byu_library")
    search_fields = ("resource_title", "owner__username", "owner__netid")

    class Media:
        js = ("js/admin_call_number.js",)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    ("owner", "date_needed"),
                    "resource_title",
                    "imdb_link",
                    ("audio_language", "subtitle_language"),
                    (
                        "checked_out_from_hbll",
                        "checked_out_from_other_byu_library",
                    ),
                    "byu_call_number",
                    (
                        "violence_or_blood_and_gore",
                        "nudity_or_sexual_content",
                    ),
                    (
                        "profanity_or_vulgarity",
                        "self_harm_or_suicide",
                        "drug_use",
                    ),
                    (
                        "acknowledged_compliance",
                        "acknowledged_fair_use_limitation",
                    ),
                )
            },
        ),
        (
            "Intake Decisions",
            {
                "fields": (
                    "resource_to_use",
                    "new_resource_imdb_id",
                    "new_resource_not_in_imdb",
                    "file_to_use",
                    (
                        "new_resource_file",
                        "new_resource_file_full_video",
                        "new_resource_file_version",
                        "new_resource_file_audio_language",
                        "new_resource_file_subtitle_language",
                        "new_resource_file_barcode",
                        "new_resource_file_has_no_barcode",
                    ),
                    "user_has_shown_proof_of_ownership",
                )
            },
        ),
    )

    @admin.display(description="Existing matches")
    def existing_matches_summary(self, obj):
        candidates = _resource_match_candidates(obj)
        if not candidates:
            return "None"
        matching_file_count = sum(
            any(
                _resource_file_matches_request(resource_file, obj)
                for resource_file in candidate[
                    "resource"
                ].resource_files.select_related(
                    "audio_language", "burned_in_subtitles_language"
                )
            )
            for candidate in candidates
        )
        return (
            f"{len(candidates)} candidate(s), {matching_file_count} with matching files"
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if "_approve_request" not in request.POST:
            return

        try:
            with transaction.atomic():
                resource = form.cleaned_data["selected_resource"]
                if resource is None:
                    resource = Resource.objects.create(
                        name=obj.resource_title,
                        media_type=Resource.MediaType.VIDEO,
                        requester_username=obj.owner.username,
                        physical_copy_exists=True,
                        imdb_id=form.cleaned_data["resolved_new_resource_imdb_id"],
                        violence_or_blood_and_gore=obj.violence_or_blood_and_gore,
                        nudity_or_sexual_content=obj.nudity_or_sexual_content,
                        profanity_or_vulgarity=obj.profanity_or_vulgarity,
                        self_harm_or_suicide=obj.self_harm_or_suicide,
                        drug_use=obj.drug_use,
                        checked_out_from_hbll=obj.checked_out_from_hbll,
                        checked_out_from_other_byu_library=(
                            obj.checked_out_from_other_byu_library
                        ),
                        byu_call_number=obj.byu_call_number,
                    )

                resource_file = form.cleaned_data["selected_resource_file"]
                if resource_file is None:
                    resource_file = form.cleaned_data["pending_resource_file"]
                    resource_file.resource = resource
                    resource_file.full_clean()
                    resource_file.save()

                ResourceAccess.objects.get_or_create(user=obj.owner, resource=resource)
                obj.generated_resource = resource
                obj.save(update_fields=["generated_resource"])
        except (IntegrityError, ValidationError) as error:
            request.intake_approval_error = str(error)
            return

        request.intake_approval_result = (resource, resource_file)

    def response_change(self, request, obj):
        if "_approve_request" not in request.POST:
            return super().response_change(request, obj)

        approval_error = getattr(request, "intake_approval_error", None)
        if approval_error:
            messages.error(
                request,
                f"The request could not be approved: {approval_error}",
            )
        else:
            resource, resource_file = request.intake_approval_result
            messages.success(
                request,
                f'Approved the request using Resource "{resource.name}" and '
                f'ResourceFile "{resource_file.version or os.path.basename(resource_file.file.name)}". '
                "Access was granted to "
                f"{obj.owner}.",
            )
        return HttpResponseRedirect(
            reverse("admin:core_resourceintakerequest_change", args=(obj.pk,))
        )


@admin.register(Playlist)
class PlaylistAdmin(VersionAdmin):
    list_display = ("name", "owner", "published", "archived", "public", "created_at")
    list_filter = ("published", "archived", "public", "created_at")
    search_fields = ("name", "owner__name", "owner__netid", "owner__username")


@admin.register(ResourceFile)
class ResourceFileAdmin(VersionAdmin):
    list_display = ("file", "resource", "version", "full_video", "created_at")
    list_filter = ("full_video", "created_at")
    search_fields = ("file", "version", "resource__name")
    readonly_fields = ("checksum", "checksum_at")


@admin.register(Content)
class ContentAdmin(VersionAdmin):
    list_display = (
        "title",
        "playlist",
        "resource",
        "published",
        "views",
        "created_at",
    )
    list_filter = (
        "published",
        "allow_definitions",
        "allow_notes",
        "allow_captions",
        "allow_fast_playback",
        "created_at",
    )
    readonly_fields = ("views",)
    search_fields = ("title", "description", "playlist__name")

    def get_form(self, request, obj=None, **kwargs):
        """Dynamically filters the 'resource_file' field's queryset.

        If editing an existing Content object, it shows only the files
        associated with resources owned by the content's playlist owner.
        If adding a new Content object, it shows no files until a playlist
        is selected and saved, guiding the user with help text.
        """
        form = super().get_form(request, obj, **kwargs)
        # If we are editing an existing Content object.
        if obj:
            # Check if the content has a playlist and the playlist has an owner.
            if obj.playlist and obj.playlist.owner:
                # Filter the 'resource_file' field to show only files whose resource is accessible
                # by the playlist's owner.
                form.base_fields[
                    "resource_file"
                ].queryset = ResourceFile.objects.filter(
                    resource__users=obj.playlist.owner
                )
            else:
                # If no playlist or owner, show no files.
                form.base_fields["resource_file"].queryset = ResourceFile.objects.none()

            # Filter clips to show only those associated with the selected file
            if obj.get_resource():
                form.base_fields["clips"].queryset = Clip.objects.filter(
                    resource=obj.get_resource()
                )
            else:
                form.base_fields["clips"].queryset = Clip.objects.none()

        else:
            # On the 'add' page, we can't filter by playlist owner yet.
            # Showing no files until a playlist is selected and saved.
            form.base_fields["resource_file"].queryset = ResourceFile.objects.none()
            form.base_fields[
                "resource_file"
            ].help_text = "Select playlist, then save to see available resource files. You will be unable to see resource files that belong to Resources that you do not have Resource Access to."

        return form


@admin.register(Course)
class CourseAdmin(VersionAdmin):
    list_display = ("dept", "catalog_number", "section_number", "created_at")
    list_filter = ("dept", "created_at")
    search_fields = ("dept", "catalog_number", "section_number")


@admin.register(Language)
class LanguageAdmin(VersionAdmin):
    list_display = ("language", "created_at")
    search_fields = ("language",)


class AnnotationAdmin(VersionAdmin):
    list_display = ("name", "track__annotation_set", "track", "created_at")
    list_filter = ("created_at",)
    search_fields = ("name", "resource__name")


@admin.register(CommentAnnotation)
class CommentAnnotationAdmin(AnnotationAdmin):
    pass


@admin.register(SkipAnnotation)
class SkipAnnotationAdmin(AnnotationAdmin):
    pass


@admin.register(MuteAnnotation)
class MuteAnnotationAdmin(AnnotationAdmin):
    pass


@admin.register(BlankAnnotation)
class BlankAnnotationAdmin(AnnotationAdmin):
    pass


@admin.register(PauseAnnotation)
class PauseAnnotationAdmin(AnnotationAdmin):
    pass


@admin.register(BlurAnnotation)
class BlurAnnotationAdmin(AnnotationAdmin):
    pass


@admin.register(Clip)
class ClipAdmin(VersionAdmin):
    pass


@admin.register(Subtitle)
class SubtitleAdmin(VersionAdmin):
    def save_model(self, request, obj, form, change):
        obj.save()
        file_name_parts = os.path.splitext(obj.subtitles_file.name)
        file_ext = file_name_parts[1]
        file_name_split = file_name_parts[0].split("/")
        file_name = file_name_split[len(file_name_split) - 1]
        if file_ext == ".srt":
            vtt_content = convert_srt_content_to_vtt(
                obj.subtitles_file.read().decode("utf-8")
            )
            new_file_name = file_name + ".vtt"
            obj.subtitles_file.delete()
            obj.save()
            obj.subtitles_file = ContentFile(content=vtt_content, name=new_file_name)
        super().save_model(request, obj, form, change)

    list_display = ("name", "language", "owner", "resource", "created_at")
    list_filter = ("language", "created_at")
    search_fields = (
        "name",
        "owner__name",
        "resource__name",
        "language__language",
    )


@admin.register(Email)
class EmailAdmin(VersionAdmin):
    list_display = ("subject", "sender", "sender_email", "sent_at")
    list_filter = ("sent_at", "created_at")
    search_fields = ("subject", "sender__name", "sender_email", "body")


@admin.register(ResourceAccess)
class ResourceAccessAdmin(VersionAdmin):
    list_display = ("user", "resource", "last_verified", "created_at")
    list_filter = ("last_verified", "created_at")
    search_fields = ("user__netid", "user__username", "resource__name")


@admin.register(PlaylistUserAccess)
class PlaylistUserAccessAdmin(VersionAdmin):
    list_display = ("user", "playlist", "playlist_role", "created_at")
    list_filter = ("playlist_role", "created_at")
    search_fields = ("user__netid", "user__username", "playlist__name")


@admin.register(ResourceFileKey)
class ResourceFileKeyAdmin(VersionAdmin):
    list_display = ("user", "resource_file", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__netid", "user__username", "resource_file__resource__name")


@admin.register(ImportantWord)
class ImportantWordAdmin(VersionAdmin):
    list_display = ("word", "translation")
    search_fields = ("word", "translation", "content__title")


@admin.register(AnnotationSet)
class AnnotationSetAdmin(VersionAdmin):
    list_display = ("name", "owner", "resource", "created_at")
    list_filter = ("created_at",)
    search_fields = ("name", "owner__netid", "owner__username", "resource__name")


@admin.register(Track)
class TrackAdmin(VersionAdmin):
    list_display = (
        "annotation_set__resource__name",
        "annotation_set__name",
        "name",
        "annotation_set__owner__netid",
        "annotation_set__owner__username",
    )
    search_fields = (
        "annotation_set__name",
        "annotation_set__owner__netid",
        "annotation_set__owner__username",
        "annotation_set__resource__name",
    )


@admin.register(UserCourses)
class UserCourses(VersionAdmin):
    list_display = ("user", "course", "yearterm")
    search_fields = ("user", "course", "yearterm")
