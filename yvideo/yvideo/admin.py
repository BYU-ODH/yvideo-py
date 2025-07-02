from django.contrib import admin
from reversion.admin import VersionAdmin

from .models import (
    Annotation,
    AuthToken,
    Clip,
    Collection,
    CollectionUserAccess,
    Content,
    Course,
    Email,
    File,
    FileKey,
    Language,
    Resource,
    ResourceAccess,
    Subtitle,
    User,
)


@admin.register(User)
class UserAdmin(VersionAdmin):
    list_display = ("netid", "name", "email", "privilege_level", "created_at")
    list_filter = ("privilege_level", "created_at")
    search_fields = ("netid", "name")


@admin.register(Resource)
class ResourceAdmin(VersionAdmin):
    list_display = (
        "name",
        "media_type",
        "requester_netid",
        "copyrighted",
        "views",
        "created_at",
    )
    list_filter = ("media_type", "copyrighted", "physical_copy_exists", "created_at")
    search_fields = ("name",)


@admin.register(Collection)
class CollectionAdmin(VersionAdmin):
    list_display = ("name", "owner", "published", "archived", "public", "created_at")
    list_filter = ("published", "archived", "public", "created_at")
    search_fields = ("name", "owner__name", "owner__netid")


@admin.register(File)
class FileAdmin(VersionAdmin):
    list_display = ("path", "version", "resource", "full_video", "created_at")
    list_filter = ("full_video", "created_at")
    search_fields = ("path", "version", "resource__name")


@admin.register(Content)
class ContentAdmin(VersionAdmin):
    list_display = ("title", "collection", "published", "views", "created_at")
    list_filter = (
        "published",
        "allow_definitions",
        "allow_notes",
        "allow_captions",
        "created_at",
    )
    search_fields = ("title", "description", "collection__name")


@admin.register(Course)
class CourseAdmin(VersionAdmin):
    list_display = ("dept", "catalog_number", "section_number", "created_at")
    list_filter = ("dept", "created_at")
    search_fields = ("dept", "catalog_number", "section_number")


@admin.register(Language)
class LanguageAdmin(VersionAdmin):
    list_display = ("language", "created_at")
    search_fields = ("language",)


@admin.register(Annotation)
class AnnotationAdmin(VersionAdmin):
    list_display = ("name", "owner", "file", "created_at")
    list_filter = ("created_at",)
    search_fields = ("name", "owner__name", "owner__netid", "file__resource__name")


@admin.register(Clip)
class ClipAdmin(VersionAdmin):
    list_display = ("name", "owner", "file", "start_time", "end_time", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "name",
        "description",
        "tags",
        "owner__name",
        "file__resource__name",
    )


@admin.register(Subtitle)
class SubtitleAdmin(VersionAdmin):
    list_display = ("name", "language", "owner", "file", "created_at")
    list_filter = ("language", "created_at")
    search_fields = (
        "name",
        "owner__name",
        "file__resource__name",
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
    search_fields = ("user__netid", "resource__name")


@admin.register(CollectionUserAccess)
class CollectionUserAccessAdmin(VersionAdmin):
    list_display = ("user", "collection", "collection_role", "created_at")
    list_filter = ("collection_role", "created_at")
    search_fields = ("user__netid", "collection__name")


@admin.register(AuthToken)
class AuthTokenAdmin(VersionAdmin):
    list_display = ("user", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__netid", "user__name")


@admin.register(FileKey)
class FileKeyAdmin(VersionAdmin):
    list_display = ("user", "file", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__netid", "file__resource__name")
