from datetime import datetime
from functools import wraps
import json
import logging
import mimetypes
import os
import re
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import redirect_to_login
from django.contrib.messages.storage.base import Message
from django.core.exceptions import NON_FIELD_ERRORS
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Count
from django.db.models import Prefetch
from django.db.models import ProtectedError
from django.db.models import Q
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseRedirect
from django.http import HttpResponseServerError
from django.http import JsonResponse
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST
import requests
import reversion

from .forms import AddUserLookupForm
from .forms import LegalComplianceForm
from .forms import PlaylistSettingsForm
from .forms import ResourceIntakeRequestForm
from .models import THUMBNAIL_COLORS
from .models import THUMBNAIL_TEXT_COLORS
from .models import Content
from .models import Course
from .models import Playlist
from .models import PlaylistRole
from .models import PlaylistUserAccess
from .models import Resource
from .models import ResourceAccess
from .models import ResourceAccessRequest
from .models import ResourceFile
from .models import ResourceFileKey
from .models import ResourceIntakeRequest
from .models import Subtitle
from .models import Thumbnail
from .models import User
from .models import UserCourses
from .models import active_yearterms
from .permissions import content_read_required
from .permissions import content_write_required
from .permissions import forbidden
from .permissions import instructor_required
from .permissions import lab_assistant_required
from .permissions import playlist_admin_required
from .permissions import playlist_read_required
from .permissions import playlist_write_required
from .utils import display_yearterm
from .youtube_utils import get_or_create_youtube_resource
from .youtube_utils import parse_youtube_video_id
from .youtube_utils import youtube_video_id_for_content

logger = logging.getLogger(__name__)

THUMBNAIL_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
THUMBNAIL_MAX_IMAGE_BYTES = 2 * 1024 * 1024
THUMBNAIL_MAX_TEXT_LENGTH = 100
# Every JPEG file starts with these bytes. The editor always uploads JPEGs, so
# anything else is not from our client.
JPEG_FILE_SIGNATURE = b"\xff\xd8\xff"


def spoof_permission_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not getattr(request, "can_spoof", False):
            return redirect_to_login(
                request.get_full_path(), reverse("oidc_authentication_init")
            )
        return view_func(request, *args, **kwargs)

    return _wrapped


def _spoof_actor(request):
    """The real authenticated user, even if `request.user` has been swapped to a
    spoofed identity by SpoofUserMiddleware for this request."""
    return getattr(request, "original_user", request.user)


def prepare_playlist_for_display(playlist):
    published_contents = Content.objects.filter(playlist=playlist).filter(
        published=True
    )
    contents_count = published_contents.count()
    parsed_playlist = {
        "pk": playlist.pk,
        "name": playlist.name,
        "items_display": f"{contents_count} published items"
        if contents_count > 1 or contents_count == 0
        else f"{contents_count} published item",
        "published_contents": published_contents,
        "thumbnail": Thumbnail.for_item(playlist),
    }
    return parsed_playlist


def thumbnail_editor_response(url_name, item):
    """Tells the client which thumbnail editor to open for a newly created item."""
    return JsonResponse({"thumbnail_editor_url": reverse(url_name, args=[item.pk])})


def breadcrumb_trail(*crumbs):
    """(label, url) pairs for partials/breadcrumbs.html, under the Playlists root.

    The last crumb is the current page and renders unlinked, so its url is ignored.
    """
    return [
        {"label": label, "url": url}
        for label, url in (("Playlists", reverse("playlists")), *crumbs)
    ]


def content_parent_crumbs(content):
    return (
        (content.playlist.name, reverse("playlist_info", args=[content.playlist_id])),
    )


def content_details_trail(content, *crumbs):
    """Trail for pages an editor reaches from a content's Details page.

    Editors enter content through Details, so its preview and editor pages hang
    below it rather than Details hanging below the player (#467).
    """
    return breadcrumb_trail(
        *content_parent_crumbs(content),
        (
            f"{content.title} Details",
            reverse("display_content_info", args=[content.pk]),
        ),
        *crumbs,
    )


@login_not_required
@require_GET
def render_user_message(request):
    text = request.GET.get("message", "")
    if not text.strip():
        return HttpResponse(b"", content_type="text/html")

    level = request.GET.get("level", "info")
    levels = {
        "debug": messages.DEBUG,
        "info": messages.INFO,
        "success": messages.SUCCESS,
        "warning": messages.WARNING,
        "error": messages.ERROR,
    }
    if level not in levels:
        text = (
            "Invalid message level, could not create message "
            f"with level ({level}) and message ({text})"
        )
        level = "error"

    return render(
        request,
        "core/partials/user_message.html",
        {"message": Message(levels[level], text)},
    )


@require_GET
def index(request):
    return HttpResponseRedirect(reverse("playlists"))


@require_GET
def about(request):
    return render(
        request,
        "core/about.html",
        {"breadcrumbs": breadcrumb_trail(("About", None))},
    )


@require_GET
def whats_new(request):
    return render(
        request,
        "core/whats_new.html",
        {"breadcrumbs": breadcrumb_trail(("What's new?", None))},
    )


@require_POST
@content_read_required
def get_player_data(request, content):
    try:
        # Editors still receive subtitles when captions are disallowed so they can work on
        # them in the video editor; everyone else never gets the tracks at all.
        include_subtitles = content.allow_captions or content.can_be_edited_by(
            request.user
        )
        player_json = content.get_player_json(include_subtitles=include_subtitles)
        has_subtitles = bool(
            any(x.get("vtt") or x.get("url") for x in player_json["subtitleTracks"])
        )

        data = {
            "annotations": player_json["annotations"],
            "subtitleTracks": player_json["subtitleTracks"],
            "has_subtitles": has_subtitles,
            "allowFastPlayback": content.allow_fast_playback,
            "allowCaptions": content.allow_captions,
            "clips": player_json["clips"],
            "clipsOnly": content.clips_only,
        }

        return JsonResponse(data)
    except Exception as e:
        logger.error(f"An error occurred while getting player data: {e}")
        return HttpResponseServerError()


@require_GET
@content_read_required
def player(request, content):
    """Render the video player page."""
    resource_file_key = request.user.get_resource_filekey(content)
    content_source_url = request.user.get_content_source_url(content)
    if not resource_file_key and not content_source_url:
        return HttpResponse(
            "User does not have permission to view this content", status=403
        )

    # Editors reach the player through Details' Preview link, so their visits are
    # checks on their own content rather than views to count.
    can_edit = content.can_be_edited_by(request.user)
    if not can_edit:
        try:
            content.views += 1
            content.save()
            content.resource.views += 1
            content.resource.save()

        except Exception as e:
            # log the error, but still provide the video in case of failure
            logger.error(
                "Failed to update views for content and associated resource. "
                f"Exception: {e}"
            )

    context = {
        "content": content,
        "resource_file_key_id": resource_file_key.id if resource_file_key else None,
        "content_source_url": content_source_url,
        "youtube_video_id": youtube_video_id_for_content(content, content_source_url),
        "allow_events": True,
        "breadcrumbs": (
            content_details_trail(content, ("Preview", None))
            if can_edit
            else breadcrumb_trail(
                *content_parent_crumbs(content), (content.title, None)
            )
        ),
    }

    return render(request, "core/player.html", context)


@require_GET
def stream_file(request, resource_file_key_id):
    """Stream file content with support for HTTP Range requests (partial content)."""
    # Keys are minted per playback by User.get_resource_filekey, which is where
    # the permission check happens. Binding the key to its user and expiring it
    # stops a key id -- sequential, and visible in the page source -- from being
    # a durable capability anyone can replay (#320).
    resource_file_key_obj = get_object_or_404(ResourceFileKey, id=resource_file_key_id)
    if not resource_file_key_obj.can_be_used_by(request.user):
        return forbidden("This media key is not valid for you, or has expired.")
    file_obj = resource_file_key_obj.resource_file

    if not file_obj.file or not os.path.exists(file_obj.file.path):
        raise Http404("File not found")

    try:
        return _file_response(request, file_obj)
    except Exception:
        logger.exception(
            "Error streaming resource file %s (key %s)",
            file_obj.pk,
            resource_file_key_id,
        )
        return HttpResponseServerError("Error streaming file.")


def _stream_content_type(file_obj, file_path):
    content_type, _ = mimetypes.guess_type(file_path)
    if content_type:
        return content_type
    name = file_obj.file.name.lower()
    if name.endswith((".mp4", ".m4v")):
        return "video/mp4"
    if name.endswith(".webm"):
        return "video/webm"
    if name.endswith((".mov", ".qt")):
        return "video/quicktime"
    if name.endswith(".mp3"):
        return "audio/mpeg"
    if name.endswith(".m4a"):
        return "audio/mp4"
    if name.endswith(".wav"):
        return "audio/wav"
    return "application/octet-stream"


# Largest slice of a file sent for one Range request. Browsers open video with
# "bytes=0-" (the whole file); answering that with a capped 206 makes them come
# back for the next slice as playback needs it, so no request holds a worker for
# a whole video. Range-capable clients, Safari/AVFoundation included, read
# Content-Range and continue from where the response ended.
MAX_RANGE_RESPONSE_BYTES = 8 * 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024


def _iter_file(file_path, start=0, length=None, chunk_size=STREAM_CHUNK_BYTES):
    """Yield the file from ``start`` in chunks: ``length`` bytes, or to EOF."""
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining is None or remaining > 0:
            size = chunk_size if remaining is None else min(chunk_size, remaining)
            chunk = f.read(size)
            if not chunk:
                break
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk


def _file_response(request, file_obj):
    file_path = file_obj.file.path
    file_size = os.path.getsize(file_path)
    content_type = _stream_content_type(file_obj, file_path)

    # Parse a Range header like "bytes=0-1023" or "bytes=1024-".
    range_match = re.match(r"bytes=(\d+)-(\d*)", request.META.get("HTTP_RANGE", ""))
    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2)) if range_match.group(2) else file_size - 1

        # A start past EOF, or a reversed range such as "bytes=5-2", cannot be
        # served. Without the reversed check, the negative read length below
        # would turn a bad client header into a garbage response or a 500.
        if start >= file_size or end < start:
            response = HttpResponse(status=416)  # Range Not Satisfiable
            response["Content-Range"] = f"bytes */{file_size}"
            return response

        end = min(end, file_size - 1, start + MAX_RANGE_RESPONSE_BYTES - 1)
        length = end - start + 1
        response = StreamingHttpResponse(
            _iter_file(file_path, start, length),
            status=206,
            content_type=content_type,
        )
        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response["Content-Length"] = str(length)
    else:
        response = StreamingHttpResponse(
            _iter_file(file_path), content_type=content_type
        )
        response["Content-Length"] = str(file_size)

    response["Accept-Ranges"] = "bytes"
    # private: the media key is bound to one user (#320), so shared caches must
    # not store the bytes and serve them to anyone else.
    response["Cache-Control"] = "private, max-age=3600"
    response["ETag"] = f'"{file_size}-{os.path.getmtime(file_path)}"'
    return response


@require_GET
def playlists(request):
    owned_playlists = []
    if request.user.is_instructor:
        owned_playlists = [
            prepare_playlist_for_display(playlist)
            for playlist in Playlist.objects.filter(owner=request.user)
        ]

    collaborator_playlists = [
        prepare_playlist_for_display(playlist)
        for playlist in request.user.get_collaborator_playlists()
    ]

    # Course-derived access expires with the term, so only terms whose grace window
    # is still open can contribute a playlist the user could actually open.
    active = active_yearterms()

    # organize assigned playlists by yearterm and then by course.
    playlists_by_course_by_yearterm = []
    for yearterm in sorted(
        UserCourses.objects.filter(user=request.user, course__yearterm__in=active)
        .values_list("course__yearterm", flat=True)
        .distinct()
    ):
        user_courses = UserCourses.objects.filter(
            user=request.user, course__yearterm=yearterm
        )
        playlists_by_course = []
        for user_course in user_courses:
            course_playlists = Playlist.objects.filter(
                courses=user_course.course, published=True, archived=False
            )
            playlists_by_course.append(
                {
                    "course_name": user_course.course.__str__(),
                    "playlists": [
                        prepare_playlist_for_display(playlist)
                        for playlist in course_playlists
                    ],
                }
            )
        playlists_by_course_by_yearterm.append(
            {
                "yearterm_display": display_yearterm(yearterm),
                "playlists_by_course": playlists_by_course,
            }
        )

    # Collaborator grants are listed above regardless of state; read-only grants get
    # the same published/archived filter, so a card is never shown that would 403.
    read_only_grant_playlists = [
        prepare_playlist_for_display(playlist)
        for playlist in Playlist.objects.filter(
            playlistuseraccess__user=request.user,
            playlistuseraccess__playlist_role=PlaylistRole.STUDENT,
            published=True,
            archived=False,
        ).exclude(owner=request.user)
    ]

    context = {
        "user": request.user,
        "is_instructor": request.user.is_instructor,
        "owned_playlists": owned_playlists,
        "collaborator_playlists": collaborator_playlists,
        "assigned_courses_by_yearterm": playlists_by_course_by_yearterm,
        "read_only_grant_playlists": read_only_grant_playlists,
        # Lab assistants act on an instructor's behalf, so the legacy differences are
        # theirs to know too.
        "show_whats_new": request.user.is_instructor or request.user.is_lab_assistant,
        "breadcrumbs": breadcrumb_trail(),
    }
    return render(request, "core/playlists.html", context)


def get_playlist_types(user):
    playlists = Playlist.objects.filter(owner=user)

    archived = playlists.filter(archived=True)
    published = playlists.filter(archived=False, published=True)
    unpublished = playlists.filter(archived=False, published=False)
    return {"archived": archived, "published": published, "unpublished": unpublished}


@require_POST
@instructor_required
def create_playlist(request):
    data = json.loads(request.body)
    if data and "name" in data:
        try:
            playlist = Playlist.objects.create(name=data["name"], owner=request.user)
            messages.success(request, "Playlist created.")
            return thumbnail_editor_response("playlist_thumbnail_editor", playlist)
        except Exception as e:
            logger.error(
                f"An error occured when the user: {request.user} attempted to create a playlist. Exception: {e}"
            )

            return HttpResponseServerError()

    else:
        return HttpResponseBadRequest()


def get_semester_and_year_options():
    # we need a list of years for the year selector when assigning playlist to course
    today = datetime.now()
    start_year = today.year - 5
    year_options = [
        {"value": x, "current": x == today.year}
        for x in range(start_year, (start_year + 10))
    ]

    # we need to make a guess about what semester it is to help the user
    month = today.month
    semester = None
    if month < 5:
        semester = 1  # winter
    elif month == 5:
        semester = 3  # spring
    elif month == 6 or month == 7:
        semester = 4  # summer
    else:
        semester = 5  # fall

    return {
        "year_options": year_options,
        "semester": semester,
        "yearterm": f"{today.year}{semester}",
    }


def get_assigned_courses(playlist, yearterm):
    courses = playlist.courses.filter(yearterm=yearterm)
    # aggregate the section numbers under the course title (course.dept course.catalog_number)
    assigned_courses_map = {}
    for course in courses:
        course_map_key = f"{course.dept} {course.catalog_number}"
        if course_map_key not in assigned_courses_map:
            assigned_courses_map[course_map_key] = {
                "sections": [],
                "dept": course.dept,
                "catalog_number": course.catalog_number,
            }
        if (
            course.section_number
            not in assigned_courses_map[course_map_key]["sections"]
        ):
            assigned_courses_map[course_map_key]["sections"].append(
                course.section_number
            )

    # prepare assigned course information for easy integration into the course_assignment.html template

    assigned_courses = []
    for title, course_info in assigned_courses_map.items():
        assigned_courses.append(
            {
                "dept": course_info["dept"],
                "catalog_number": course_info["catalog_number"],
                "section_list": ", ".join(course_info["sections"]),
            }
        )
    return assigned_courses


@require_GET
@playlist_read_required
def playlist_info(request, playlist):
    return render_playlist_info(request, playlist)


def render_playlist_info(request, playlist):
    try:
        can_edit = playlist.can_be_edited_by(request.user)
        contents = Content.objects.filter(playlist=playlist).select_related("resource")
        if not can_edit:
            contents = contents.filter(published=True)
        contents = [(content, Thumbnail.for_item(content)) for content in contents]
        year_and_semester = get_semester_and_year_options()
        assigned_courses = get_assigned_courses(playlist, year_and_semester["yearterm"])

        context = {
            "playlist": playlist,
            "contents": contents,
            "thumbnail": Thumbnail.for_item(playlist) if can_edit else None,
            "can_edit": can_edit,
            "can_administer": playlist.can_be_administered_by(request.user),
            "form": PlaylistSettingsForm(instance=playlist),
            "year_options": year_and_semester["year_options"],
            "semester": year_and_semester["semester"],
            "assigned_courses": assigned_courses,
            "breadcrumbs": breadcrumb_trail((playlist.name, None)),
        }
        return render(request, "core/playlist_info.html", context)
    except Exception as e:
        logger.error(f"Failed to retrieve playlist info. Exception: {e}")
        return HttpResponseServerError()


@require_GET
@playlist_write_required
def display_playlist_settings(request, playlist):
    try:
        form = PlaylistSettingsForm(instance=playlist)
        year_and_semester = get_semester_and_year_options()

        context = {
            "playlist": playlist,
            "form": form,
            "thumbnail": Thumbnail.for_item(playlist),
            "semester": year_and_semester["semester"],
            "year_options": year_and_semester["year_options"],
        }
        return render(request, "core/partials/playlist_settings.html", context)
    except Exception as e:
        logger.error(f"Failed to render playlist settings. Exception: {e}")
        return HttpResponseServerError()


@require_POST
@playlist_write_required
def render_course_assignment(request, playlist):
    try:
        parsed_data = json.loads(request.body)
        semester = parsed_data["semester"]
        year = parsed_data["year"]

        # perform some minimal sanitation since we are passing this value into the db
        if len(semester) > 1 or len(year) > 4:
            return HttpResponseBadRequest()

        return render_course_assignment_partial(request, playlist, f"{year}{semester}")

    except Exception as e:
        logger.error(f"Failed to render course assignment information. Exception: {e}")
        return HttpResponseServerError()


def render_course_assignment_partial(request, playlist, yearterm):
    return render(
        request,
        "core/partials/course_assignment.html",
        {"assigned_courses": get_assigned_courses(playlist, yearterm)},
    )


@require_POST
@playlist_write_required
def assign_playlist_to_course(request, playlist):
    # first check if the course already exists. if so, add the playlist to that course.
    # otherwise, create the course and then add the playlist
    try:
        parsed_data = json.loads(request.body)
        if (
            "dept" not in parsed_data
            or "catalog_number" not in parsed_data
            or "sections" not in parsed_data
            or "year" not in parsed_data
            or "semester" not in parsed_data
        ):
            logger.error(
                "Failed to assign course to playlist because of insufficient data provided"
            )
            return HttpResponseBadRequest()

        dept = parsed_data["dept"]
        catalog_number = parsed_data["catalog_number"]
        section_numbers = parsed_data["sections"]
        yearterm = f"{parsed_data['year']}{parsed_data['semester']}"
        with transaction.atomic():
            for section_number in section_numbers:
                existing_course_filter = Course.objects.filter(
                    dept=dept,
                    catalog_number=catalog_number,
                    section_number=section_number,
                    yearterm=yearterm,
                )
                if existing_course_filter.count() < 1:
                    existing_course = Course.objects.create(
                        dept=dept,
                        catalog_number=catalog_number,
                        section_number=section_number,
                        yearterm=yearterm,
                    )
                else:
                    if existing_course_filter.count() > 1:
                        logger.error(
                            f"More than one course was returned when assigning a playlist ({playlist}) to a course. Assigning to the first result"
                        )
                    existing_course = existing_course_filter.first()
                playlist.courses.add(existing_course)
                playlist.save()
        return render_course_assignment_partial(request, playlist, yearterm)

    except Exception as e:
        logger.error(f"Failed to assign course to playlist. Exception: {e}")
        return HttpResponseServerError()


@require_POST
@playlist_write_required
def update_playlist_course_sections(request, playlist):
    try:
        parsed_data = json.loads(request.body)
        if (
            "sections" not in parsed_data
            or "dept" not in parsed_data
            or "catalog_number" not in parsed_data
            or "semester" not in parsed_data
            or "year" not in parsed_data
        ):
            logger.error(
                "Failed to update course sections because of insufficient data provided"
            )
            return HttpResponseBadRequest()
        new_sections_list = parsed_data["sections"]
        dept = parsed_data["dept"]
        catalog_number = parsed_data["catalog_number"]
        yearterm = f"{parsed_data['year']}{parsed_data['semester']}"
        courses = Course.objects.filter(
            dept=dept, catalog_number=catalog_number, yearterm=yearterm
        )

        with transaction.atomic():
            # it is possible a course with a provided section_number doesn't exist yet. if that is true,
            # create it
            existing_section_numbers = []
            for course in courses:
                existing_section_numbers.append(course.section_number)
            for new_section_number in new_sections_list:
                if new_section_number not in existing_section_numbers:
                    Course.objects.create(
                        dept=dept,
                        catalog_number=catalog_number,
                        yearterm=yearterm,
                        section_number=new_section_number,
                    )

            # Associate the provided sections with the playlist.
            # We could go through and figure out exactly which should be removed and which should be added,
            # but it is probably more robust to simply remove all associations for this dept, catalog_number, and yearterm
            # and then add all the sections provided by the user.
            playlist.courses.remove(*courses)
            new_courses = Course.objects.filter(
                dept=dept,
                catalog_number=catalog_number,
                yearterm=yearterm,
                section_number__in=new_sections_list,
            )
            playlist.courses.add(*new_courses)

        return HttpResponse()

    except Exception as e:
        logger.error(f"Failed to update course sections. Exception: {e}")
        return HttpResponseServerError()


@require_POST
@playlist_write_required
def unassign_playlist_from_course(request, playlist):
    try:
        parsed_data = json.loads(request.body)
        if (
            "dept" not in parsed_data
            or "catalog_number" not in parsed_data
            or "semester" not in parsed_data
            or "year" not in parsed_data
        ):
            logger.error(
                "Failed to remove playlist from course because of insufficient data"
            )
            return HttpResponseBadRequest()

        courses = playlist.courses.all().filter(
            dept=parsed_data["dept"],
            catalog_number=parsed_data["catalog_number"],
            yearterm=f"{parsed_data['year']}{parsed_data['semester']}",
        )
        playlist.courses.remove(*courses)
        return HttpResponse()

    except Exception as e:
        logger.error(f"Failed to remove playlist assigned to course. Exception: {e}")
        return HttpResponseServerError()


@require_POST
@playlist_write_required
def update_playlist_settings(request, playlist):
    form = PlaylistSettingsForm(request.POST)
    if form.is_valid():
        try:
            playlist.name = form.cleaned_data["name"]
            playlist.archived = form.cleaned_data["archived"]
            if form.cleaned_data["archived"]:
                playlist.published = False
            else:
                playlist.published = form.cleaned_data["published"]
            # Only IC editors see (and so submit) the field.
            if request.user.is_ic_editor:
                playlist.is_ic = form.cleaned_data["is_ic"]
            playlist.save()
            messages.success(request, "Playlist settings saved.")
            return render_playlist_info(request, playlist)
        except Exception as e:
            logger.error(
                f"An error occured while attempting to update playlist settings. {e}"
            )
            return HttpResponseServerError()
    else:
        return HttpResponseBadRequest()


def scheduled_in_ic_programs(subject):
    """409 explaining why: ICProgramItem protects its Content, so deleting one that an
    International Cinema program shows would otherwise surface as a generic failure."""
    return HttpResponse(
        f"{subject} is scheduled in an International Cinema program. "
        "Remove it from the program first.",
        status=409,
        content_type="text/plain",
    )


GRANTABLE_PLAYLIST_ROLES = (
    PlaylistRole.COLLABORATOR,
    PlaylistRole.STUDENT,
)

PLAYLIST_ROLE_DESCRIPTIONS = {
    PlaylistRole.COLLABORATOR: (
        "Co-instructor or TA. Can edit this playlist and its videos, but cannot "
        "delete it or manage people."
    ),
    PlaylistRole.STUDENT: "Can watch published videos. Cannot make changes.",
}


def grantable_roles_for(playlist, user):
    """The roles `user` may hand out on `playlist`, in the order the picker shows them."""
    return [
        {
            "value": role.value,
            "label": role.label,
            "description": PLAYLIST_ROLE_DESCRIPTIONS[role],
        }
        for role in GRANTABLE_PLAYLIST_ROLES
        if playlist.can_grant_role(user, role)
    ]


def get_course_access_summary(playlist):
    """Courses that reach this playlist, and how many students that is.

    The count mirrors Playlist.can_be_viewed_by exactly -- distinct users holding a
    UserCourses row on a course assigned here whose yearterm is active. Counting
    any other way would advertise access the app does not actually grant.
    """
    yearterms = active_yearterms()
    # One grouped query rather than a COUNT per course: this runs on the playlist page for
    # every editor, and a playlist assigned to a dozen sections paid a dozen round trips.
    # distinct=True because a student enrolled in two sections of the same course is one
    # person who can see it, which is what can_be_viewed_by would say.
    counts = (
        playlist.courses.filter(yearterm__in=yearterms)
        .values("dept", "catalog_number")
        .annotate(student_count=Count("usercourses__user", distinct=True))
        .order_by("dept", "catalog_number")
    )

    sections = {}
    for course in playlist.courses.filter(yearterm__in=yearterms).order_by(
        "section_number"
    ):
        sections.setdefault((course.dept, course.catalog_number), []).append(
            course.section_number
        )

    return [
        {
            "name": f"{row['dept']} {row['catalog_number']}",
            "section_list": ", ".join(sections[(row["dept"], row["catalog_number"])]),
            "section_count": len(sections[(row["dept"], row["catalog_number"])]),
            "student_count": row["student_count"],
        }
        for row in counts
    ]


def playlist_members_context(request, playlist):
    """Everything the Manage People panel renders.

    Each row carries its own may_manage flag rather than a single page-level one: an
    owner may touch every row, but a collaborator may touch only the read-only ones, and deciding
    that here keeps the template and the endpoints agreeing on one answer.
    """
    accesses = (
        PlaylistUserAccess.objects.filter(playlist=playlist)
        .exclude(user_id=playlist.owner_id)
        .select_related("user")
        .order_by("playlist_role", "user__last_name", "user__username")
    )
    # A stray role integer degrades to its number instead of raising: migrations 0011 and
    # 0014 and map_legacy_collection_role cover the values we know about (legacy auditor
    # and TA), but this now renders on the playlist page itself, so an unknown one would
    # take the whole page down rather than just this panel.
    labels = {role.value: role.label for role in PlaylistRole}
    members = [
        {
            "user": access.user,
            "role": access.playlist_role,
            "role_label": labels.get(access.playlist_role, str(access.playlist_role)),
            "may_manage": playlist.can_grant_role(request.user, access.playlist_role),
        }
        for access in accesses
    ]
    return {
        "playlist": playlist,
        "members": members,
        "grantable_roles": grantable_roles_for(playlist, request.user),
        "default_role": PlaylistRole.STUDENT.value,
        "course_access": get_course_access_summary(playlist),
    }


def render_playlist_members_list(request, playlist):
    return render(
        request,
        "core/partials/playlist_members_list.html",
        playlist_members_context(request, playlist),
    )


def render_playlist_members_roster(request, playlist):
    """Just the rows -- the only part an add or remove changes.

    Returning the whole panel instead would replace the status live region along with
    it, and a live region swapped out in the same breath as the message it should be
    announcing does not announce.
    """
    return render(
        request,
        "core/partials/playlist_members_roster.html",
        playlist_members_context(request, playlist),
    )


@require_GET
@playlist_write_required
def render_playlist_members(request, playlist):
    """The Manage People panel, fetched when the dialog opens.

    Deliberately not rendered with the playlist page: the roster and the course-access
    counts are queries every editor would pay on every visit for a dialog most visits
    never open. Fetching on open also means the panel reflects the database rather than
    whenever the page was last loaded.
    """
    return render_playlist_members_list(request, playlist)


# Substring matching over every user means one character returns a page of real names and
# NetIDs, which is a directory to scrape rather than a search. Two is still short enough
# for anyone who knows who they are looking for.
MEMBER_SEARCH_MINIMUM_LENGTH = 2


@require_POST
@playlist_write_required
def playlist_member_search(request, playlist):
    """Existing users matching the typed text, minus everyone already on the playlist."""
    query = request.POST.get("search", "").strip()
    users = User.objects.none()
    if len(query) >= MEMBER_SEARCH_MINIMUM_LENGTH:
        users = (
            User.objects.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(username__icontains=query)
                | Q(netid__icontains=query)
            )
            .exclude(pk=playlist.owner_id)
            .exclude(playlistuseraccess__playlist=playlist)
            .order_by("last_name", "username")[:25]
        )
    return render(
        request,
        "core/partials/playlist_member_options_for_select.html",
        {
            "users": users,
            "query": query,
            # So a one-character query says "keep typing" rather than "No matches",
            # which would read as "this person is not in Y-Video" and send someone to
            # the directory lookup for someone who is already here.
            "query_too_short": 0 < len(query) < MEMBER_SEARCH_MINIMUM_LENGTH,
        },
    )


MEMBER_TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"


def _member_error(message):
    return HttpResponseBadRequest(message, content_type=MEMBER_TEXT_CONTENT_TYPE)


def _member_forbidden(message):
    """403 the panel can show verbatim.

    text/plain rather than the default text/html, because the client shows a body only
    when the content type says we wrote it -- an HTML body could be Django's own error
    page, and announcing one of those reads an entire document into a live region.
    """
    return forbidden(message, content_type=MEMBER_TEXT_CONTENT_TYPE)


def _member_access_or_404(playlist, user_id):
    """The membership row these endpoints may act on.

    The owner is excluded rather than special-cased later: their authority comes from
    Playlist.owner, so any row they happen to also hold is not what grants it, and
    editing that row would suggest otherwise.
    """
    return get_object_or_404(
        PlaylistUserAccess.objects.exclude(user_id=playlist.owner_id),
        playlist=playlist,
        user_id=user_id,
    )


def _resolve_member_to_add(request):
    """The user being added, either picked from the search results or looked up by id.

    Returns (user, error_message, warning). The lookup form provisions from BYU's
    directory, so a collaborator who has never signed in to Y-Video can still be given a role
    before the term starts -- the reason the picker accepts a raw NetID at all.

    The warning is the form's enrollment_warning: a provisioned account whose enrollment
    could not be synced still gets the role, but its course-based access will be wrong
    until the sync catches up. The admin surfaces this and so must we, or the person who
    added them learns about it from a student who cannot see the playlist.
    """
    user_id = request.POST.get("user_id", "").strip()
    if user_id:
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            return None, "That person no longer has an account.", None
        return user, None, None

    identifier = request.POST.get("identifier", "").strip()
    if not identifier:
        return None, "Search for a person, or type a NetID or 9-digit BYU ID.", None

    form = AddUserLookupForm({"identifier": identifier})
    if not form.is_valid():
        return (
            None,
            " ".join(form.errors.get("identifier", ["That lookup failed."])),
            None,
        )
    return form.resolved_user, None, getattr(form, "enrollment_warning", None)


@require_POST
@playlist_write_required
def add_playlist_member(request, playlist):
    try:
        role = PlaylistRole(int(request.POST.get("role", "")))
    except (TypeError, ValueError):
        return _member_error("Choose a role.")
    if role not in GRANTABLE_PLAYLIST_ROLES:
        return _member_error("Choose a role.")
    if not playlist.can_grant_role(request.user, role):
        return _member_forbidden("Only the playlist owner can grant that role.")

    user, error, warning = _resolve_member_to_add(request)
    if error:
        return _member_error(error)

    if user.pk == playlist.owner_id:
        return _member_error(
            f"{user.get_full_name() or user.username} owns this playlist already."
        )

    _, created = PlaylistUserAccess.objects.get_or_create(
        user=user,
        playlist=playlist,
        defaults={"playlist_role": role},
    )
    if not created:
        return _member_error(
            f"{user.get_full_name() or user.username} already has a role here. "
            "Change it in the list above."
        )

    response = render_playlist_members_roster(request, playlist)
    if warning:
        # A header rather than the body, because the body is the roster fragment the
        # client swaps in. The client appends this to its own "added" announcement.
        # Flattened first: Django rejects a header containing a newline outright, and a
        # 500 here would lose the grant's confirmation over an advisory message.
        response["X-Member-Warning"] = " ".join(warning.split())
    return response


@require_POST
@playlist_write_required
def update_playlist_member_role(request, playlist, user_id):
    access = _member_access_or_404(playlist, user_id)
    try:
        new_role = PlaylistRole(int(request.POST.get("role", "")))
    except (TypeError, ValueError):
        return _member_error("Choose a role.")
    if new_role not in GRANTABLE_PLAYLIST_ROLES:
        return _member_error("Choose a role.")

    # Both ends, because demoting a collaborator is revoking a collaborator grant.
    # Checking only the new role would let a collaborator quietly strip another.
    if not (
        playlist.can_grant_role(request.user, access.playlist_role)
        and playlist.can_grant_role(request.user, new_role)
    ):
        return _member_forbidden("Only the playlist owner can change that role.")

    access.playlist_role = new_role
    access.save(update_fields=["playlist_role", "updated_at"])
    return HttpResponse(
        f"{access.user.get_full_name() or access.user.username} is now "
        f"{new_role.label}.",
        content_type="text/plain; charset=utf-8",
    )


@require_POST
@playlist_write_required
def remove_playlist_member(request, playlist, user_id):
    access = _member_access_or_404(playlist, user_id)
    if not playlist.can_grant_role(request.user, access.playlist_role):
        return _member_forbidden("Only the playlist owner can remove that person.")

    access.delete()
    return render_playlist_members_roster(request, playlist)


def get_playlist_contents(playlist):
    contents = Content.objects.filter(playlist=playlist)
    published = contents.filter(published=True)
    unpublished = contents.filter(published=False)
    return {"published": published, "unpublished": unpublished}


@require_http_methods(["DELETE"])
@playlist_admin_required
def delete_playlist(request, playlist):
    try:
        playlist.delete()
        messages.success(request, "Playlist deleted.")
        return HttpResponse()
    except ProtectedError:
        return scheduled_in_ic_programs("This playlist")
    except Exception as e:
        logger.error(
            f"An error occured while deleting the playlist with id: {playlist.pk}. Exception: {e}"
        )
        return HttpResponseServerError()


@require_POST
@playlist_write_required
def create_content(request, playlist):
    try:
        parsed_data = json.loads(request.body)
        if "title" not in parsed_data or "resource_file_id" not in parsed_data:
            logger.error(
                "Failed to create new content because of invalid data provided."
            )
            return HttpResponseBadRequest()

        resource_file = ResourceFile.objects.get(pk=parsed_data["resource_file_id"])
        if not request.user.can_access_resource(resource_file.resource):
            return forbidden("You do not have access to that resource.")
        if resource_file.is_projection_file:
            return forbidden(
                "Projection masters are for the International Cinema booth only."
            )
        content = Content.objects.create(
            playlist=playlist,
            title=parsed_data["title"],
            resource_file=resource_file,
        )
        messages.success(request, "Video added to playlist.")
        return thumbnail_editor_response("content_thumbnail_editor", content)
    except ResourceFile.DoesNotExist:
        logger.error("Failed to create new content due to missing ResourceFile")
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"An error occured while creating a new Content. Exception: {e}")
        return HttpResponseServerError()


@require_POST
@playlist_write_required
def create_content_from_youtube_url(request, playlist):
    """Create URL-only Content from a YouTube URL, self-serve (no lab-assistant/
    admin gate) - see core/youtube.py for the Resource get-or-create logic."""
    try:
        parsed_data = json.loads(request.body)
        if "title" not in parsed_data or "url" not in parsed_data:
            logger.error(
                "Failed to create new content from URL because of invalid data provided."
            )
            return HttpResponseBadRequest()

        video_id = parse_youtube_video_id(parsed_data["url"])
        if not video_id:
            logger.error(
                "Failed to create new content because the URL was not a "
                "recognized YouTube URL"
            )
            return HttpResponseBadRequest(
                "That is not a YouTube video URL we can use. Regular video and "
                "youtu.be links work; Shorts do not."
            )

        resource = get_or_create_youtube_resource(video_id, request.user.username)
        content = Content.objects.create(
            playlist=playlist,
            title=parsed_data["title"],
            url=parsed_data["url"],
            resource=resource,
        )
        messages.success(request, "YouTube video added to playlist.")
        return thumbnail_editor_response("content_thumbnail_editor", content)
    except json.JSONDecodeError:
        logger.error(
            "Failed to create new content from URL because of a malformed body"
        )
        return HttpResponseBadRequest()
    except Playlist.DoesNotExist:
        logger.error("Failed to create new content due to missing Playlist")
        return HttpResponseBadRequest()
    except IntegrityError as e:
        # Resource.name is unique, and the get-or-create above keys on imdb_id, so a Resource
        # that already carries this video's generated name under a *different* id lands here.
        # Reported rather than swallowed as a 500: it is fixable (rename the other Resource),
        # and only an admin can fix it, so the message has to reach someone.
        logger.error(
            f"Could not provision a Resource for YouTube video {video_id}: {e}"
        )
        return HttpResponse(
            "A resource already exists under the name this video would use. An "
            "administrator needs to rename it before this video can be added.",
            status=409,
        )
    except Exception as e:
        logger.error(
            f"An error occured while creating new YouTube content. Exception: {e}"
        )
        return HttpResponseServerError()


@require_GET
@playlist_write_required
def render_create_from_resource_resources(request, playlist):
    return render(
        request,
        "core/partials/resource_list.html",
        {"resources": request.user.accessible_resources()},
    )


@require_POST
@playlist_write_required
def render_create_from_resource_form(request, playlist, resource_id):
    try:
        resource = get_object_or_404(Resource, pk=resource_id)
        if not request.user.can_access_resource(resource):
            return forbidden("You do not have access to that resource.")

        context = {
            "playlist_id": playlist.pk,
            "options": resource.resource_files.filter(is_projection_file=False),
        }
        return render(request, "core/partials/create_from_resource_form.html", context)
    except Exception as e:
        logger.error(f"Failed to render the create from resource form. Exception: {e}")
        return HttpResponseServerError()


@require_http_methods(["DELETE"])
@content_write_required
def delete_content(request, content):
    try:
        contents_count = Content.objects.filter(playlist=content.playlist).count()
        content.delete()
        messages.success(request, "Video removed from playlist.")
        if contents_count <= 1:
            return HttpResponse(
                "There is no published content for this playlist", status=200
            )
        else:
            return HttpResponse("", status=200)
    except ProtectedError:
        return scheduled_in_ic_programs("This video")
    except Exception as e:
        logger.error(
            f"An error occured while deleting content with id: {content.pk}. Exception: {e}"
        )
        return HttpResponseServerError()


@require_GET
@content_read_required
def display_content_info(request, content):
    try:
        resource_file_key = request.user.get_resource_filekey(content)
        content_source_url = request.user.get_content_source_url(content)
        context = {
            "content": content,
            "content_id": content.pk,
            "thumbnail": Thumbnail.for_item(content),
            "can_edit": content.can_be_edited_by(request.user),
            "resource_file_key_id": resource_file_key.pk if resource_file_key else None,
            "content_source_url": content_source_url,
            "youtube_video_id": youtube_video_id_for_content(
                content, content_source_url
            ),
            "content_has_clips": content.has_clips(),
            "subtitle_options": content.get_subtitles(),
            "breadcrumbs": content_details_trail(content),
        }
        return render(request, "core/content_info.html", context)
    except Exception as e:
        logger.error(f"Failed to display content settings. Exception: {e}")
        return HttpResponseServerError()


@require_GET
@content_write_required
def render_content_settings_form(request, content):
    try:
        context = {
            "content": content,
            "content_has_clips": content.has_clips(),
            "subtitle_options": content.get_subtitles(),
        }
        return render(request, "core/partials/content_settings_form.html", context)
    except Exception as e:
        logger.error(f"Failed to render content settings form. Exception: {e}")
        return HttpResponseServerError()


@require_POST
@content_write_required
def update_content(request, content):
    try:
        data = json.loads(request.body)
        content.title = data["title"]
        content.description = data["description"]
        content.allow_definitions = data["allow_definitions"]
        content.allow_captions = data["allow_captions"]
        content.allow_fast_playback = data["allow_fast_playback"]
        content.clips_only = data["clips_only"]
        content.published = data["published"]

        default_subtitle_id = data.get("default_subtitle_track_id")
        if default_subtitle_id:
            resource = content.resource
            content.default_subtitle_track = Subtitle.objects.filter(
                pk=default_subtitle_id, resource=resource
            ).first()
        else:
            content.default_subtitle_track = None

        content.save()
        messages.success(request, "Video settings saved.")
        return HttpResponse()
    except Exception as e:
        logger.error(f"An error occured while updating content. Exception: {e}")
        messages.error(request, "Failed to save video settings.")
        return HttpResponseServerError()


@require_GET
@login_not_required
def invalid_login(request):
    return render(request, "core/invalid_login.html", {})


@require_POST
@spoof_permission_required
def spoof_user_start(request):
    spoof_user_id = request.POST.get("spoof_user_id")
    if spoof_user_id:
        actor = _spoof_actor(request)
        target_user = (
            User.objects.filter(pk=spoof_user_id).first()
            if spoof_user_id.isdigit()
            else None
        )
        if target_user and actor.can_spoof_as(target_user):
            request.session["spoof_user_id"] = target_user.pk
        else:
            target_desc = (
                f"{target_user.first_name} {target_user.last_name} "
                f"({target_user.netid} {target_user.username})"
                if target_user
                else f"unknown user id {spoof_user_id}"
            )
            logger.warning(
                f"SPOOF DENIED: {actor.first_name} {actor.last_name} "
                f"({actor.netid} {actor.username}) attempted to spoof as {target_desc}"
            )
    return redirect(request.POST.get("next") or request.headers.get("Referer") or "/")


@require_POST
@spoof_permission_required
def spoof_user_stop(request):
    request.session.pop("spoof_user_id", None)
    return redirect(request.POST.get("next") or request.headers.get("Referer") or "/")


@require_POST
@spoof_permission_required
def spoof_user_search(request):
    actor = _spoof_actor(request)
    query = request.POST.get("search", "").strip()
    users = User.objects.filter(
        (
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(username__icontains=query)
        )
        & ~Q(id=actor.id)
    )
    if not actor.is_superuser:
        # Lab assistants may spoof any non-admin user, but never an admin.
        users = users.exclude(is_superuser=True)
    users = users.order_by("last_name")[:25]
    html = render_to_string(
        "core/partials/spoof_user_options_for_select.html", {"users": users}
    )
    return HttpResponse(html)


SEARCH_RESULT_LIMIT = 25


def _barcoded_files(lookup):
    return Prefetch(
        lookup,
        queryset=ResourceFile.objects.filter(is_projection_file=False, barcode__gt=""),
        to_attr="barcoded_files",
    )


def _search_resources(user, query):
    """Library resources matching a title or barcode, flagged with the user's status."""
    resources = list(
        Resource.objects.exclude(media_type=Resource.MediaType.WEB)
        .filter(
            Q(name__icontains=query)
            | Q(name_disambiguator__icontains=query)
            | Q(resource_files__barcode=query)
        )
        .distinct()
        .order_by("name", "name_disambiguator")
        .prefetch_related(_barcoded_files("resource_files"))[:SEARCH_RESULT_LIMIT]
    )
    accessible_ids = set(
        user.accessible_resources()
        .filter(pk__in=[r.pk for r in resources])
        .values_list("pk", flat=True)
    )
    pending_ids = set(
        ResourceAccessRequest.objects.filter(
            user=user, resource__in=resources
        ).values_list("resource_id", flat=True)
    )
    for resource in resources:
        resource.user_has_access = resource.pk in accessible_ids
        resource.access_requested = resource.pk in pending_ids
    return resources


@require_POST
@instructor_required
def request_resource_access(request, resource_id):
    resource = get_object_or_404(
        Resource.objects.exclude(media_type=Resource.MediaType.WEB), pk=resource_id
    )
    compliance = LegalComplianceForm(request.POST)
    if not compliance.is_valid():
        messages.error(
            request,
            "Please agree to the legal compliance terms before requesting access.",
        )
    elif not request.user.can_access_resource(resource):
        # The revision keeps a record of who agreed to the terms after approval
        # deletes the request.
        with reversion.create_revision():
            ResourceAccessRequest.objects.get_or_create(
                user=request.user, resource=resource, defaults=compliance.cleaned_data
            )
            reversion.set_user(_spoof_actor(request))
        messages.success(
            request,
            f"Your request for access to {resource} has been submitted. Please bring "
            "your physical copy of this resource to the Humanities Learning Commons "
            "(HLC) as proof of ownership.",
        )
    query = request.POST.get("q") or resource.name
    return redirect(f"{reverse('request_resource')}?{urlencode({'q': query})}")


@require_GET
@lab_assistant_required
def resource_access_requests(request):
    pending = (
        ResourceAccessRequest.objects.select_related("user", "resource")
        .prefetch_related(_barcoded_files("resource__resource_files"))
        .order_by("created_at")
    )
    intake_requests = (
        ResourceIntakeRequest.objects.filter(generated_resource__isnull=True)
        .select_related("owner")
        .order_by("date_needed", "created_at")
    )
    return render(
        request,
        "core/resource_access_requests.html",
        {"access_requests": pending, "intake_requests": intake_requests},
    )


@require_POST
@lab_assistant_required
def approve_resource_access_request(request, access_request_id):
    access_request = get_object_or_404(ResourceAccessRequest, pk=access_request_id)
    with reversion.create_revision():
        ResourceAccess.objects.update_or_create(
            user_id=access_request.user_id,
            resource_id=access_request.resource_id,
            defaults={"last_verified": timezone.now()},
        )
        access_request.delete()
        reversion.set_user(_spoof_actor(request))
        reversion.set_comment("Approved resource access request")
    messages.success(
        request,
        f"Granted {access_request.user.get_full_name()} access to "
        f"{access_request.resource}.",
    )
    return redirect("resource_access_requests")


@require_http_methods(["GET", "POST"])
@instructor_required
def request_resource(request):
    query = request.GET.get("q", "").strip()
    title_collision_error = False
    if request.method == "POST":
        form = ResourceIntakeRequestForm(request.POST)
        if form.is_valid():
            content_request = form.save(commit=False)
            content_request.owner = request.user
            content_request.save()
            messages.success(
                request,
                "Your resource request has been submitted. Please bring your VHS, "
                "DVD, Blu-ray, or other physical copy of this resource to the "
                "Humanities Learning Commons (HLC) so we can begin processing it.",
            )
            return redirect("request_resource")
        else:
            title_collision_error = form.has_error(
                NON_FIELD_ERRORS, code="unique_together"
            )

        for field_name in form.errors:
            if field_name in form.fields:
                widget = form.fields[field_name].widget
                widget.attrs["class"] = (
                    f"{widget.attrs.get('class', '')} invalid-input".strip()
                )
    else:
        form = ResourceIntakeRequestForm(initial={"resource_title": query})

    results = _search_resources(request.user, query) if query else []
    return render(
        request,
        "core/partials/resource_intake_request.html",
        {
            "form": form,
            "title_collision_error": title_collision_error,
            "query": query,
            "results": results,
            "any_requestable": any(
                not r.user_has_access and not r.access_requested for r in results
            ),
            # Its ids must differ from the intake form's copy of the same checkboxes.
            "access_form": LegalComplianceForm(auto_id="access-%s"),
        },
    )


def _thumbnail_youtube_video_id(request, item):
    if not isinstance(item, Content):
        return None
    return youtube_video_id_for_content(item, request.user.get_content_source_url(item))


def _youtube_thumbnail_url(video_id):
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


# Thumbnails saved before these palettes existed may use some other color. Offering it
# as an extra choice keeps it from being replaced when the user saves other changes.
def _thumbnail_palette(colors, current):
    if current.lower() in (hexcode for _, hexcode in colors):
        return colors
    return [*colors, ("Current color", current)]


def _render_thumbnail_editor(request, item, save_url):
    stream_url = None
    if isinstance(item, Content) and item.resource_file_id:
        key = request.user.get_resource_filekey(item)
        stream_url = reverse("stream_file", args=[key.pk]) if key else None
    video_id = _thumbnail_youtube_video_id(request, item)
    thumbnail = Thumbnail.for_item(item)
    context = {
        "dialog_id": "thumbnail-editor-dialog",
        "thumbnail": thumbnail,
        "background_palette": _thumbnail_palette(
            THUMBNAIL_COLORS, thumbnail.background_color
        ),
        "text_palette": _thumbnail_palette(
            THUMBNAIL_TEXT_COLORS, thumbnail.text_settings["color"]
        ),
        "save_url": save_url,
        "stream_url": stream_url,
        "youtube_image_url": _youtube_thumbnail_url(video_id) if video_id else None,
    }
    return render(request, "core/partials/modals/thumbnail_editor.html", context)


def _save_thumbnail(request, item):
    field = Thumbnail.field_for(item)
    thumbnail = getattr(item, "thumbnail", None) or Thumbnail(**{field: item})

    background_color = request.POST.get("background_color", "")
    text_color = request.POST.get("text_color", "")
    text = request.POST.get("text", "").strip()
    try:
        text_size = float(request.POST.get("text_size", ""))
    except ValueError:
        text_size = -1
    if not 0.75 <= text_size <= 4:
        return HttpResponseBadRequest("Invalid text size.")
    if not (
        THUMBNAIL_HEX_COLOR_RE.fullmatch(background_color)
        and THUMBNAIL_HEX_COLOR_RE.fullmatch(text_color)
    ):
        return HttpResponseBadRequest("Invalid color.")
    if len(text) > THUMBNAIL_MAX_TEXT_LENGTH:
        return HttpResponseBadRequest(
            f"Text must be {THUMBNAIL_MAX_TEXT_LENGTH} characters or fewer."
        )

    image_source = request.POST.get("image_source", "keep")
    if image_source == "upload":
        upload = request.FILES.get("image")
        if upload is None or upload.size > THUMBNAIL_MAX_IMAGE_BYTES:
            return HttpResponseBadRequest("Image is missing or too large.")
        image = upload.read()
        if not image.startswith(JPEG_FILE_SIGNATURE):
            return HttpResponseBadRequest("Image must be a JPEG.")
        thumbnail.image = image
    elif image_source == "youtube":
        video_id = _thumbnail_youtube_video_id(request, item)
        if not video_id:
            return HttpResponseBadRequest("This content has no YouTube video.")
        try:
            response = requests.get(_youtube_thumbnail_url(video_id), timeout=10)
            response.raise_for_status()
            thumbnail.image = response.content
        except requests.RequestException as e:
            logger.warning(f"Could not fetch YouTube thumbnail for {video_id}: {e}")
            return HttpResponse(
                "Could not get the YouTube thumbnail. Try again later.", status=502
            )
    elif image_source == "none":
        thumbnail.image = None
    elif image_source != "keep":
        logger.warning(f"Invalid thumbnail image source: {image_source!r}")
        return HttpResponseBadRequest()

    thumbnail.background_color = background_color
    thumbnail.text = {
        "color": text_color,
        "size": text_size,
    }
    # Unchanged default text is left unsaved so the tile keeps following renames.
    if text != thumbnail.default_text():
        thumbnail.text["text"] = text
    thumbnail.save()
    return HttpResponse(status=204)


def _thumbnail_image(item):
    thumbnail = getattr(item, "thumbnail", None)
    if thumbnail is None or thumbnail.image is None:
        return HttpResponse(status=404)
    response = HttpResponse(bytes(thumbnail.image), content_type="image/jpeg")
    # Image URLs carry a version, so a stored copy never goes stale.
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


@require_GET
@playlist_write_required
def playlist_thumbnail_editor(request, playlist):
    return _render_thumbnail_editor(
        request, playlist, reverse("save_playlist_thumbnail", args=[playlist.pk])
    )


@require_POST
@playlist_write_required
def save_playlist_thumbnail(request, playlist):
    return _save_thumbnail(request, playlist)


@require_GET
@content_write_required
def content_thumbnail_editor(request, content):
    return _render_thumbnail_editor(
        request, content, reverse("save_content_thumbnail", args=[content.pk])
    )


@require_POST
@content_write_required
def save_content_thumbnail(request, content):
    return _save_thumbnail(request, content)


@require_GET
@playlist_read_required
def playlist_thumbnail_image(request, playlist):
    return _thumbnail_image(playlist)


@require_GET
@content_read_required
def content_thumbnail_image(request, content):
    return _thumbnail_image(content)
