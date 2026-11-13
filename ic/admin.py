"""Django admin for International Cinema (production; the booth has no admin).

IC editors work in /ic/manage/, which also shows media jobs and uploads. The admin is
for superusers: a program's revision history.
"""

from django.contrib import admin
from reversion.admin import VersionAdmin

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
