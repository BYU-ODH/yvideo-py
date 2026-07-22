import json
import logging
import os

from django import forms
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import NoReverseMatch
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from reversion.admin import VersionAdmin

from .forms import AddUserLookupForm
from .legacy_migration import LegacyMigrationFileAction
from .legacy_migration import LegacyMigrationFileDecision
from .legacy_migration import LegacyMigrationIssue
from .legacy_migration import LegacyMigrationJob
from .legacy_migration import LegacyMigrationJobStatus
from .legacy_migration import LegacyMigrationKind
from .legacy_migration import LegacyMigrationRequest
from .legacy_migration import LegacyMigrationResource
from .legacy_migration import LegacyMigrationService
from .legacy_migration import LegacyMigrationStatus
from .legacy_migration import LegacyMigrationUserResolution
from .legacy_migration import LegacySourceMap
from .models import AnnotationSet
from .models import BlankAnnotation
from .models import BlurAnnotation
from .models import Clip
from .models import Collection
from .models import CollectionRole
from .models import CollectionUserAccess
from .models import CommentAnnotation
from .models import Content
from .models import Course
from .models import Email
from .models import ImportantWord
from .models import Language
from .models import MuteAnnotation
from .models import PauseAnnotation
from .models import Resource
from .models import ResourceAccess
from .models import ResourceFile
from .models import ResourceFileKey
from .models import SkipAnnotation
from .models import Subtitle
from .models import Track
from .models import User
from .models import UserCourses
from .utils import convert_srt_content_to_vtt

logger = logging.getLogger(__name__)


def _pretty_json(value):
    if not value:
        return "-"
    return json.dumps(value, indent=2, sort_keys=True, default=str)


class LegacyMigrationFileDecisionForm(forms.ModelForm):
    class Meta:
        model = LegacyMigrationFileDecision
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get("action")
        selected_existing_resource_file = cleaned_data.get(
            "selected_existing_resource_file"
        )

        if (
            action == LegacyMigrationFileAction.REUSE_EXISTING
            and not selected_existing_resource_file
        ):
            raise forms.ValidationError(
                "Choose an existing resource file when file action is 'Reuse Existing'."
            )

        if (
            action != LegacyMigrationFileAction.REUSE_EXISTING
            and selected_existing_resource_file
        ):
            raise forms.ValidationError(
                "Selected existing resource file is only used with 'Reuse Existing'."
            )
        return cleaned_data


class LegacyMigrationFileDecisionInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        reused_targets_by_resource = {}

        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            action = form.cleaned_data.get("action")
            if action != LegacyMigrationFileAction.REUSE_EXISTING:
                continue

            selected_existing_resource_file = form.cleaned_data.get(
                "selected_existing_resource_file"
            )
            migration_resource = form.cleaned_data.get("migration_resource")
            if not migration_resource:
                migration_resource = getattr(form.instance, "migration_resource", None)
            if not selected_existing_resource_file or not migration_resource:
                continue

            target_resource_id = selected_existing_resource_file.resource_id
            existing_target_id = reused_targets_by_resource.get(migration_resource.pk)
            if existing_target_id and existing_target_id != target_resource_id:
                raise forms.ValidationError(
                    "All reused files for a legacy resource must point to the same "
                    "existing resource."
                )
            reused_targets_by_resource[migration_resource.pk] = target_resource_id


@admin.register(User)
class UserAdmin(VersionAdmin):
    list_display = (
        "username",
        "netid",
        "first_name",
        "last_name",
        "email",
        "privilege_level",
        "date_joined",
    )
    list_filter = ("privilege_level", "date_joined")
    search_fields = ("username", "netid", "first_name", "last_name")
    add_form_template = "admin/core/user/add_form.html"

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


@admin.register(Collection)
class CollectionAdmin(VersionAdmin):
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
        "collection",
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
        "created_at",
    )
    readonly_fields = ("views",)
    search_fields = ("title", "description", "collection__name")

    def get_form(self, request, obj=None, **kwargs):
        """Dynamically filters the 'resource_file' field's queryset.

        If editing an existing Content object, it shows only the files
        associated with resources owned by the content's collection owner.
        If adding a new Content object, it shows no files until a collection
        is selected and saved, guiding the user with help text.
        """
        form = super().get_form(request, obj, **kwargs)
        # If we are editing an existing Content object.
        if obj:
            # Check if the content has a collection and the collection has an owner.
            if obj.collection and obj.collection.owner:
                # Filter the 'resource_file' field to show only files whose resource is accessible
                # by the collection's owner.
                form.base_fields[
                    "resource_file"
                ].queryset = ResourceFile.objects.filter(
                    resource__users=obj.collection.owner
                )
            else:
                # If no collection or owner, show no files.
                form.base_fields["resource_file"].queryset = ResourceFile.objects.none()

            # Filter clips to show only those associated with the selected file
            if obj.get_resource():
                form.base_fields["clips"].queryset = Clip.objects.filter(
                    resource=obj.get_resource()
                )
            else:
                form.base_fields["clips"].queryset = Clip.objects.none()

        else:
            # On the 'add' page, we can't filter by collection owner yet.
            # Showing no files until a collection is selected and saved.
            form.base_fields["resource_file"].queryset = ResourceFile.objects.none()
            form.base_fields[
                "resource_file"
            ].help_text = "Select collection, then save to see available resource files. You will be unable to see resource files that belong to Resources that you do not have Resource Access to."

            # No clips until resource_file is selected and saved.
            form.base_fields["clips"].queryset = Clip.objects.none()
            form.base_fields[
                "clips"
            ].help_text = "Select a resource_file and save to see available clips."

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
    list_display = ("name", "owner", "resource", "start_time", "end_time", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "name",
        "description",
        "tags",
        "owner__name",
        "resource__name",
    )


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


@admin.register(CollectionUserAccess)
class CollectionUserAccessAdmin(VersionAdmin):
    list_display = ("user", "collection", "collection_role", "created_at")
    list_filter = ("collection_role", "created_at")
    search_fields = ("user__netid", "user__username", "collection__name")


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


class LegacyMigrationResourceInline(admin.TabularInline):
    model = LegacyMigrationResource
    extra = 0
    fields = (
        "legacy_name",
        "target_resource_name",
        "selected_existing_resource",
        "legacy_media_type",
        "resource_access_preview",
        "include",
        "is_synthetic",
        "fuzzy_matches_preview",
    )
    readonly_fields = (
        "legacy_name",
        "legacy_media_type",
        "resource_access_preview",
        "is_synthetic",
        "fuzzy_matches_preview",
    )

    def resource_access_preview(self, obj):
        matching_resource = next(
            (
                resource_row
                for resource_row in obj.request.raw_snapshot.get("resources", [])
                if resource_row.get("legacy_resource_id") == obj.legacy_resource_id
            ),
            None,
        )
        if not matching_resource:
            return "-"

        access_rows = matching_resource.get("resource_access", [])
        if not access_rows:
            return "-"

        identities = []
        for access_row in access_rows:
            identity = (
                access_row.get("username")
                or access_row.get("email")
                or access_row.get("byu_person_id")
                or access_row.get("legacy_user_id")
                or "Unknown user"
            )
            if identity not in identities:
                identities.append(identity)
        return ", ".join(sorted(identities)) or "-"

    resource_access_preview.short_description = "Resource Access"

    def fuzzy_matches_preview(self, obj):
        if not obj.fuzzy_matches:
            return "-"
        lines = []
        for match in obj.fuzzy_matches:
            lines.append(f"<li>{match['resource_name']} ({match['score']})</li>")
        return mark_safe(f"<ul>{''.join(lines)}</ul>")

    fuzzy_matches_preview.short_description = "Similar Resources"


class LegacyMigrationFileDecisionInline(admin.TabularInline):
    model = LegacyMigrationFileDecision
    form = LegacyMigrationFileDecisionForm
    formset = LegacyMigrationFileDecisionInlineFormSet
    extra = 0
    fields = (
        "migration_resource_label",
        "legacy_version",
        "target_version",
        "size_display",
        "modified_display",
        "last_accessed_display",
        "device_inode_display",
        "absolute_path_display",
        "legacy_path",
        "candidate_matches_preview",
        "action",
        "selected_existing_resource_file",
    )
    readonly_fields = (
        "migration_resource_label",
        "legacy_version",
        "size_display",
        "modified_display",
        "last_accessed_display",
        "device_inode_display",
        "absolute_path_display",
        "legacy_path",
        "candidate_matches_preview",
    )

    def migration_resource_label(self, obj):
        return obj.migration_resource.legacy_name

    migration_resource_label.short_description = "Legacy Resource"

    def size_display(self, obj):
        if obj.size_bytes is None:
            return "Unavailable"
        return f"{obj.size_bytes:,} bytes"

    size_display.short_description = "Size"

    def modified_display(self, obj):
        if not obj.mtime_at:
            return "Unavailable"
        return obj.mtime_at

    modified_display.short_description = "Modified"

    def last_accessed_display(self, obj):
        if not obj.atime_at:
            return "Unavailable"
        return obj.atime_at

    last_accessed_display.short_description = "Last Accessed"

    def device_inode_display(self, obj):
        if obj.device is None or obj.inode is None:
            return "Unavailable"
        return f"{obj.device}:{obj.inode}"

    device_inode_display.short_description = "Device/Inode"

    def absolute_path_display(self, obj):
        return obj.metadata.get("absolute_path") or "Unavailable"

    absolute_path_display.short_description = "Absolute Path"

    def candidate_matches_preview(self, obj):
        if not obj.candidate_matches:
            return "-"
        lines = []
        for match in obj.candidate_matches:
            lines.append(
                "<li>"
                f"{match['resource_name']} / {match['version']} "
                f"[{match['match_reason']}] "
                f"size={match['size_bytes']:,} "
                f"path={match['path']} "
                "</li>"
            )
        return mark_safe(f"<ul>{''.join(lines)}</ul>")

    candidate_matches_preview.short_description = "Candidate Matches"


class LegacyMigrationUserResolutionInline(admin.TabularInline):
    model = LegacyMigrationUserResolution
    extra = 0
    fields = (
        "legacy_username",
        "legacy_byu_id",
        "legacy_email",
        "roles_display",
        "contexts_display",
        "resolution_status",
        "matched_user",
        "notes",
    )
    readonly_fields = (
        "legacy_username",
        "legacy_byu_id",
        "legacy_email",
        "roles_display",
        "contexts_display",
    )

    @admin.display(description="Roles")
    def roles_display(self, obj):
        return ", ".join(obj.roles) or "-"

    @admin.display(description="Contexts")
    def contexts_display(self, obj):
        return ", ".join(obj.contexts) or "-"


class LegacyMigrationIssueInline(admin.TabularInline):
    model = LegacyMigrationIssue
    extra = 0
    fields = ("severity", "code", "message", "details_display")
    readonly_fields = ("severity", "code", "message", "details_display")
    can_delete = False

    @admin.display(description="Details")
    def details_display(self, obj):
        return format_html(
            "<pre style='white-space:pre-wrap;margin:0;'>{}</pre>",
            _pretty_json(obj.details),
        )


class LegacyMigrationJobInline(admin.TabularInline):
    model = LegacyMigrationJob
    extra = 0
    fields = (
        "job_type",
        "status",
        "current_phase",
        "attempts",
        "started_at",
        "finished_at",
        "last_error",
    )
    readonly_fields = (
        "job_type",
        "status",
        "current_phase",
        "attempts",
        "started_at",
        "finished_at",
        "last_error",
    )
    can_delete = False


class LegacySourceMapInline(admin.TabularInline):
    model = LegacySourceMap
    extra = 0
    fields = ("source_type", "source_id", "target_model", "target_id")
    readonly_fields = ("source_type", "source_id", "target_model", "target_id")
    can_delete = False


@admin.register(LegacyMigrationRequest)
class LegacyMigrationRequestAdmin(VersionAdmin):
    class Media:
        js = ("js/legacy_migration_admin.js",)

    list_display = (
        "request_uuid",
        "migration_kind",
        "status_display",
        "requested_by",
        "target_owner",
        "created_at",
    )
    list_filter = ("migration_kind", "status", "created_at")
    search_fields = ("request_uuid", "legacy_reference", "legacy_identifier")
    readonly_fields = (
        "request_uuid",
        "legacy_identifier",
        "active_job_summary",
        "snapshot_summary",
        "snapshot_collection_access_preview",
        "snapshot_courses_preview",
        "snapshot_contents_preview",
        "raw_snapshot_display",
        "created_targets",
        "preflight_completed_at",
        "imported_at",
        "created_at",
        "updated_at",
    )
    actions = (
        "run_preflight_action",
        "refresh_issues_action",
        "approve_and_queue_action",
        "retry_latest_failed_job_action",
        "cancel_jobs_action",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "request_uuid",
                    "requested_by",
                    "target_owner",
                    "migration_kind",
                    "legacy_reference",
                    "legacy_identifier",
                    "status",
                    "request_notes",
                    "admin_notes",
                    "target_collection_name",
                    "target_collection_published",
                    "target_collection_archived",
                    "target_collection_public",
                    "active_job_summary",
                    "latest_job_error",
                )
            },
        ),
        (
            "Preflight Snapshot",
            {
                "fields": (
                    "snapshot_summary",
                    "snapshot_collection_access_preview",
                    "snapshot_courses_preview",
                    "snapshot_contents_preview",
                    "raw_snapshot_display",
                )
            },
        ),
        ("Import Results", {"fields": ("created_targets",)}),
        (
            "Timestamps",
            {
                "fields": (
                    "preflight_completed_at",
                    "imported_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
    inlines = (
        LegacyMigrationResourceInline,
        LegacyMigrationFileDecisionInline,
        LegacyMigrationUserResolutionInline,
        LegacyMigrationIssueInline,
        LegacyMigrationJobInline,
        LegacySourceMapInline,
    )

    @admin.display(description="Status", ordering="status")
    def status_display(self, obj):
        label = obj.get_status_display()
        if obj.status not in (
            LegacyMigrationStatus.QUEUED,
            LegacyMigrationStatus.RUNNING,
        ):
            return label
        active_job = (
            obj.jobs.filter(
                status__in=(
                    LegacyMigrationJobStatus.QUEUED,
                    LegacyMigrationJobStatus.RUNNING,
                )
            )
            .order_by("-created_at")
            .first()
        )
        if not active_job:
            return label
        return f"{label} ({active_job.get_job_type_display()})"

    @admin.display(description="Active Job")
    def active_job_summary(self, obj):
        active_job = (
            obj.jobs.filter(
                status__in=(
                    LegacyMigrationJobStatus.QUEUED,
                    LegacyMigrationJobStatus.RUNNING,
                )
            )
            .order_by("-created_at")
            .first()
        )
        if not active_job:
            return "No preflight or import job is currently queued or running."
        phase = (
            f" — {active_job.get_current_phase_display()}"
            if active_job.current_phase
            else ""
        )
        return (
            f"{active_job.get_job_type_display()} job is "
            f"{active_job.get_status_display().lower()}{phase}."
        )

    @admin.display(description="Raw Snapshot (debugging)")
    def raw_snapshot_display(self, obj):
        if not obj.raw_snapshot:
            return "No preflight snapshot yet."
        return format_html(
            "<pre style='white-space:pre-wrap;max-height:400px;overflow:auto;'>{}</pre>",
            _pretty_json(obj.raw_snapshot),
        )

    def save_model(self, request, obj, form, change):
        if not obj.requested_by:
            obj.requested_by = request.user
        if not obj.target_owner:
            obj.target_owner = obj.requested_by
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        service = LegacyMigrationService(require_catalog=False)
        service.sync_resource_reuse_targets(form.instance)
        service.sync_request_issues(form.instance)

    @admin.display(description="Snapshot Summary")
    def snapshot_summary(self, obj):
        if not obj.raw_snapshot:
            return "No preflight snapshot yet."
        resource_count = obj.migration_resources.count()
        file_count = obj.file_decisions.count()
        blocking_count = obj.issues.filter(severity="blocking").count()
        warning_count = obj.issues.filter(severity="warning").count()
        collection_name = ""
        if obj.migration_kind == LegacyMigrationKind.COLLECTION:
            collection_name = obj.raw_snapshot.get("collection", {}).get("name", "")
        return format_html(
            "<strong>Collection:</strong> {}<br>"
            "<strong>Resources:</strong> {}<br>"
            "<strong>Files:</strong> {}<br>"
            "<strong>Blocking Issues:</strong> {}<br>"
            "<strong>Warnings:</strong> {}",
            collection_name or "-",
            resource_count,
            file_count,
            blocking_count,
            warning_count,
        )

    @admin.display(description="Collection Access")
    def snapshot_collection_access_preview(self, obj):
        access_rows = obj.raw_snapshot.get("collection_access", [])
        if not access_rows:
            return "No collection access rows in the snapshot."

        lines = []
        for access_row in access_rows:
            identity = (
                access_row.get("username")
                or access_row.get("email")
                or access_row.get("byu_person_id")
                or access_row.get("legacy_user_id")
                or "Unknown user"
            )
            try:
                role_label = CollectionRole(int(access_row["account_role"])).label
            except (KeyError, TypeError, ValueError):
                role_label = access_row.get("account_role", "Unknown")
            lines.append(f"<li>{identity} ({role_label})</li>")
        return mark_safe(f"<ul>{''.join(lines)}</ul>")

    @admin.display(description="Course Associations")
    def snapshot_courses_preview(self, obj):
        course_rows = obj.raw_snapshot.get("courses", [])
        if not course_rows:
            return "No course associations in the snapshot."

        lines = []
        for course_row in course_rows:
            department = (course_row.get("department") or "").upper()
            catalog_number = str(course_row.get("catalog_number") or "").zfill(3)
            section_number = str(course_row.get("section_number") or "").zfill(3)
            lines.append(f"<li>{department} {catalog_number}-{section_number}</li>")
        return mark_safe(f"<ul>{''.join(lines)}</ul>")

    @admin.display(description="Contents")
    def snapshot_contents_preview(self, obj):
        content_rows = obj.raw_snapshot.get("contents", [])
        if not content_rows:
            return "No contents in the snapshot."

        lines = []
        for content_row in content_rows[:10]:
            title = content_row.get("title") or "Untitled content"
            resource_id = content_row.get("resource_id") or "No resource"
            lines.append(f"<li>{title} ({resource_id})</li>")
        if len(content_rows) > 10:
            lines.append(f"<li>... and {len(content_rows) - 10} more</li>")
        return mark_safe(f"<ul>{''.join(lines)}</ul>")

    @admin.display(description="Imported Targets")
    def created_targets(self, obj):
        if not obj.source_maps.exists():
            return "No imported objects yet."
        lines = []
        for source_map in obj.source_maps.all().order_by("source_type", "source_id"):
            try:
                admin_url = reverse(
                    f"admin:core_{source_map.target_model.lower()}_change",
                    args=[source_map.target_id],
                )
                target_display = format_html(
                    '<a href="{}">{}:{}</a>',
                    admin_url,
                    source_map.target_model,
                    source_map.target_id,
                )
            except NoReverseMatch:
                target_display = f"{source_map.target_model}:{source_map.target_id}"
            lines.append(
                "<li>"
                f"{source_map.source_type}:{source_map.source_id} -> "
                f"{target_display}"
                "</li>"
            )
        return mark_safe(f"<ul>{''.join(lines)}</ul>")

    def _report_action_error(self, request, migration_request, action_label, exc):
        logger.exception(
            "%s failed for legacy migration request %s",
            action_label,
            migration_request.request_uuid,
        )
        self.message_user(
            request,
            (
                f"{action_label} failed for request {migration_request.request_uuid}: "
                f"{exc}"
            ),
            level=messages.ERROR,
        )

    def _report_action_summary(
        self,
        request,
        action_label,
        success_count,
        failure_count,
        success_message,
    ):
        if success_count and failure_count:
            self.message_user(
                request,
                f"{success_message} {failure_count} request(s) failed.",
                level=messages.WARNING,
            )
            return
        if failure_count:
            self.message_user(
                request,
                f"{action_label} failed for {failure_count} request(s).",
                level=messages.ERROR,
            )
            return
        if success_count:
            self.message_user(
                request,
                success_message,
                level=messages.SUCCESS,
            )

    def _run_preflight(self, request, migration_request):
        """Preflight a single request. Returns True on success, False on failure."""
        try:
            LegacyMigrationService().preflight_request(migration_request)
        except Exception as exc:
            migration_request.status = LegacyMigrationStatus.PREFLIGHT_FAILED
            migration_request.latest_job_error = str(exc)
            migration_request.save(
                update_fields=["status", "latest_job_error", "updated_at"]
            )
            self._report_action_error(request, migration_request, "Preflight", exc)
            return False
        return True

    def _refresh_issues(self, request, migration_request):
        """Refresh issues for a single request. Returns True on success, False on failure."""
        service = LegacyMigrationService(require_catalog=False)
        try:
            service.sync_request_issues(migration_request)
        except Exception as exc:
            self._report_action_error(request, migration_request, "Issue refresh", exc)
            return False
        return True

    def _approve_and_queue(self, request, migration_request):
        """Approve and queue a single request. Returns True on success, False on failure."""
        service = LegacyMigrationService(require_catalog=False)
        try:
            service.approve_and_queue_import(migration_request)
        except Exception as exc:
            migration_request.latest_job_error = str(exc)
            migration_request.save(update_fields=["latest_job_error", "updated_at"])
            self._report_action_error(request, migration_request, "Approval", exc)
            return False
        return True

    def _retry_latest_failed_job(self, request, migration_request):
        """Retry the latest failed job for a single request.

        Returns True on success, False on failure, None if there is no
        failed job to retry.
        """
        try:
            latest_failed_job = (
                migration_request.jobs.filter(status="failed")
                .order_by("-created_at")
                .first()
            )
            if not latest_failed_job:
                return None
            migration_request.queue_job(latest_failed_job.job_type)
            migration_request.status = LegacyMigrationStatus.QUEUED
            migration_request.save(update_fields=["status", "updated_at"])
        except Exception as exc:
            migration_request.latest_job_error = str(exc)
            migration_request.save(update_fields=["latest_job_error", "updated_at"])
            self._report_action_error(request, migration_request, "Retry", exc)
            return False
        return True

    def _cancel_jobs(self, request, migration_request):
        """Cancel queued/running jobs for a single request. Returns True on success, False on failure."""
        try:
            migration_request.jobs.filter(status__in=("queued", "running")).update(
                status="canceled"
            )
            migration_request.status = LegacyMigrationStatus.CANCELED
            migration_request.save(update_fields=["status", "updated_at"])
        except Exception as exc:
            migration_request.latest_job_error = str(exc)
            migration_request.save(update_fields=["latest_job_error", "updated_at"])
            self._report_action_error(request, migration_request, "Cancel", exc)
            return False
        return True

    @admin.action(description="Run preflight now")
    def run_preflight_action(self, request, queryset):
        processed = 0
        failed = 0
        for migration_request in queryset:
            if self._run_preflight(request, migration_request):
                processed += 1
            else:
                failed += 1
        self._report_action_summary(
            request,
            "Preflight",
            processed,
            failed,
            f"Ran preflight for {processed} request(s).",
        )

    @admin.action(description="Refresh issues after user/file edits")
    def refresh_issues_action(self, request, queryset):
        processed = 0
        failed = 0
        for migration_request in queryset:
            if self._refresh_issues(request, migration_request):
                processed += 1
            else:
                failed += 1
        self._report_action_summary(
            request,
            "Issue refresh",
            processed,
            failed,
            f"Refreshed issues for {processed} request(s).",
        )

    @admin.action(description="Approve and queue import")
    def approve_and_queue_action(self, request, queryset):
        processed = 0
        failed = 0
        for migration_request in queryset:
            if self._approve_and_queue(request, migration_request):
                processed += 1
            else:
                failed += 1
        self._report_action_summary(
            request,
            "Approval",
            processed,
            failed,
            f"Approved and queued {processed} request(s).",
        )

    @admin.action(description="Retry latest failed job")
    def retry_latest_failed_job_action(self, request, queryset):
        processed = 0
        failed = 0
        for migration_request in queryset:
            result = self._retry_latest_failed_job(request, migration_request)
            if result is True:
                processed += 1
            elif result is False:
                failed += 1
        self._report_action_summary(
            request,
            "Retry",
            processed,
            failed,
            f"Queued retries for {processed} request(s).",
        )

    @admin.action(description="Cancel queued/running jobs")
    def cancel_jobs_action(self, request, queryset):
        processed = 0
        failed = 0
        for migration_request in queryset:
            if self._cancel_jobs(request, migration_request):
                processed += 1
            else:
                failed += 1
        self._report_action_summary(
            request,
            "Cancel",
            processed,
            failed,
            f"Canceled jobs for {processed} request(s).",
        )

    # Change-form buttons below reuse the same per-object helpers as the
    # bulk actions above, so behavior stays identical whether triggered from
    # the changelist dropdown or from the change form for a single request.
    _CHANGE_FORM_BUTTON_HANDLER_NAMES = (
        "_run_preflight",
        "_refresh_issues",
        "_approve_and_queue",
        "_retry_latest_failed_job",
        "_cancel_jobs",
    )

    def response_change(self, request, obj):
        for post_key in self._CHANGE_FORM_BUTTON_HANDLER_NAMES:
            if post_key in request.POST:
                handler = getattr(self, f"_handle{post_key}_button")
                handler(request, obj)
                return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)

    def _handle_run_preflight_button(self, request, migration_request):
        if self._run_preflight(request, migration_request):
            self.message_user(
                request,
                f"Ran preflight for request {migration_request.request_uuid}.",
                level=messages.SUCCESS,
            )

    def _handle_refresh_issues_button(self, request, migration_request):
        if self._refresh_issues(request, migration_request):
            self.message_user(
                request,
                f"Refreshed issues for request {migration_request.request_uuid}.",
                level=messages.SUCCESS,
            )

    def _handle_approve_and_queue_button(self, request, migration_request):
        if self._approve_and_queue(request, migration_request):
            self.message_user(
                request,
                (
                    f"Approved and queued request {migration_request.request_uuid} "
                    "for import."
                ),
                level=messages.SUCCESS,
            )

    def _handle_retry_latest_failed_job_button(self, request, migration_request):
        result = self._retry_latest_failed_job(request, migration_request)
        if result is True:
            self.message_user(
                request,
                f"Queued a retry for request {migration_request.request_uuid}.",
                level=messages.SUCCESS,
            )
        elif result is None:
            self.message_user(
                request,
                (
                    f"No failed job to retry for request "
                    f"{migration_request.request_uuid}."
                ),
                level=messages.INFO,
            )

    def _handle_cancel_jobs_button(self, request, migration_request):
        if self._cancel_jobs(request, migration_request):
            self.message_user(
                request,
                (
                    "Canceled queued/running jobs for request "
                    f"{migration_request.request_uuid}."
                ),
                level=messages.SUCCESS,
            )
