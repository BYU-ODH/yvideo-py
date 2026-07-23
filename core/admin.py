import os

from django.contrib import admin
from django.core.files.base import ContentFile
from reversion.admin import VersionAdmin

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
from .models import SkipAnnotation
from .models import Subtitle
from .models import Track
from .models import User
from .models import UserCourses
from .utils import convert_srt_content_to_vtt


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
        ResourceAccess.objects.create(user=user, resource=obj)


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
    list_display = ("title", "playlist", "published", "views", "created_at")
    list_filter = (
        "published",
        "allow_definitions",
        "allow_notes",
        "allow_captions",
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
