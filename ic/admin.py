"""Django admin for International Cinema (production; the booth has no admin).

IC editors work in /ic/manage/, which also shows media jobs and uploads. The admin is
for superusers: a program's revision history, and issuing instance tokens.
"""

from django.contrib import admin
from django.contrib import messages
from reversion.admin import VersionAdmin

from .models import ICInstance
from .models import ICInstanceFile
from .models import ICProgram
from .models import ICProgramItem


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


class ICInstanceFileInline(admin.TabularInline):
    model = ICInstanceFile
    extra = 0
    readonly_fields = ("resource_file", "xxh64", "verified_at", "hashed_from_bytes")
    can_delete = False


@admin.register(ICInstance)
class ICInstanceAdmin(admin.ModelAdmin):
    list_display = ("name", "last_seen_at")
    readonly_fields = (
        "last_seen_at",
        "last_status",
        "last_applied_manifest_hash",
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

    @admin.action(description="Issue a new sync token (the old one stops working)")
    def issue_new_token(self, request, queryset):
        for instance in queryset:
            raw = instance.issue_token()
            instance.save(update_fields=["token_hash"])
            self._show_token(request, instance, raw)
