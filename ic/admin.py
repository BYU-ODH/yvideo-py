"""Django admin for International Cinema (production; the booth has no admin).

IC editors work in /ic/manage/. The admin is for superusers: support, debugging, and
issuing instance tokens, which is an infrastructure task rather than an IC one.
"""

from django.contrib import admin
from django.contrib import messages
from reversion.admin import VersionAdmin

from .models import ICInstance
from .models import ICInstanceFile
from .models import ICProgram
from .models import ICProgramItem
from .models import ICSyncRequest
from .models import ICUpload
from .models import MediaJob


class ICProgramItemInline(admin.TabularInline):
    model = ICProgramItem
    extra = 0
    ordering = ("position", "id")
    autocomplete_fields = ("content",)


@admin.register(ICProgram)
class ICProgramAdmin(VersionAdmin):
    list_display = ("name", "start_date", "end_date")
    list_filter = ("start_date",)
    search_fields = ("name",)
    inlines = [ICProgramItemInline]


@admin.register(ICProgramItem)
class ICProgramItemAdmin(VersionAdmin):
    list_display = ("program", "position", "content", "mode", "subtitles")
    list_filter = ("mode", "subtitles")
    autocomplete_fields = ("content",)


@admin.register(MediaJob)
class MediaJobAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "status",
        "resource_file",
        "progress",
        "created_at",
        "finished_at",
    )
    list_filter = ("kind", "status")
    readonly_fields = [f.name for f in MediaJob._meta.fields]


@admin.register(ICUpload)
class ICUploadAdmin(admin.ModelAdmin):
    list_display = (
        "filename",
        "resource",
        "owner",
        "status",
        "received_bytes",
        "expected_size",
        "updated_at",
    )
    list_filter = ("status",)
    readonly_fields = [f.name for f in ICUpload._meta.fields]


class ICInstanceFileInline(admin.TabularInline):
    model = ICInstanceFile
    extra = 0
    readonly_fields = ("resource_file", "xxh64", "verified_at", "hashed_from_bytes")
    can_delete = False


@admin.register(ICInstance)
class ICInstanceAdmin(admin.ModelAdmin):
    list_display = ("name", "profile", "agent_url", "last_seen_at")
    readonly_fields = (
        "last_seen_at",
        "last_status",
        "last_applied_manifest_hash",
        "last_notified_manifest_hash",
        "last_notified_at",
        "last_notify_error",
        "created_at",
    )
    actions = ["issue_new_token"]
    inlines = [ICInstanceFileInline]

    def save_model(self, request, obj, form, change):
        new = not obj.token_hash
        if new:
            raw = obj.issue_token()
        super().save_model(request, obj, form, change)
        if new:
            self._show_token(request, obj, raw)

    def _show_token(self, request, obj, raw):
        messages.warning(
            request,
            f"Sync token for {obj.name} (shown once; put it in the booth's "
            f"secret_settings.IC_SYNC_TOKEN): {raw}",
        )
        if obj.agent_url and not obj.agent_secret:
            messages.warning(
                request,
                f"{obj.name} has an agent URL but no secret: add it to "
                f'IC_INSTANCE_AGENT_SECRETS["{obj.name}"] on production and '
                "IC_AGENT_SECRET on the booth.",
            )

    @admin.action(description="Issue a new sync token (the old one stops working)")
    def issue_new_token(self, request, queryset):
        for instance in queryset:
            raw = instance.issue_token()
            instance.save(update_fields=["token_hash"])
            self._show_token(request, instance, raw)


@admin.register(ICSyncRequest)
class ICSyncRequestAdmin(admin.ModelAdmin):
    list_display = (
        "instance",
        "requested_by_name",
        "status",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "instance")
    readonly_fields = [f.name for f in ICSyncRequest._meta.fields]
