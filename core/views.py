from datetime import datetime
from functools import wraps
import json
import logging
import mimetypes
import os
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import redirect_to_login
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseRedirect
from django.http import HttpResponseServerError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .forms import ContentForm
from .forms import ImportantWordForm
from .forms import PlaylistSettingsForm
from .forms import ResourceIntakeRequestForm
from .models import Content
from .models import Course
from .models import ImportantWord
from .models import Playlist
from .models import PlaylistRole
from .models import PlaylistUserAccess
from .models import Resource
from .models import ResourceFile
from .models import ResourceFileKey
from .models import Subtitle
from .models import User
from .models import UserCourses
from .models import active_yearterms
from .permissions import content_read_required
from .permissions import content_write_required
from .permissions import forbidden
from .permissions import important_word_write_required
from .permissions import instructor_required
from .permissions import playlist_admin_required
from .permissions import playlist_read_required
from .permissions import playlist_write_required
from .youtube_utils import get_or_create_youtube_resource
from .youtube_utils import parse_youtube_video_id
from .youtube_utils import youtube_video_id_for_content

logger = logging.getLogger(__name__)


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
        "items_display": f"{contents_count} items"
        if contents_count > 1 or contents_count == 0
        else f"{contents_count} item",
        "published_contents": published_contents,
    }
    return parsed_playlist


def display_yearterm(yearterm):
    term_decoder = {"1": "Winter", "3": "Spring", "4": "Summer", "5": "Fall"}
    year_string = yearterm[0:4]
    term_string = yearterm[4:]
    try:
        term_name = term_decoder[term_string]
    except KeyError:
        logger.error(f"Invalid yearterm: {yearterm}")
        return yearterm
    return f"{term_name} {year_string}"


def breadcrumb_trail(*crumbs):
    """(label, url) pairs for partials/breadcrumbs.html, under the Playlists root.

    The last crumb is the current page and renders unlinked, so its url is ignored.
    """
    return [
        {"label": label, "url": url}
        for label, url in (("Playlists", reverse("playlists")), *crumbs)
    ]


def content_parent_crumbs(content):
    """Content.playlist is nullable, so the parent crumb is not always available."""
    if content.playlist_id is None:
        return ()
    return (
        (content.playlist.name, reverse("playlist_info", args=[content.playlist_id])),
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
        player_json = content.get_player_json()
        has_subtitles = bool(
            any(x.get("vtt") or x.get("url") for x in player_json["subtitleTracks"])
        )

        data = {
            "annotations": player_json["annotations"],
            "subtitleTracks": player_json["subtitleTracks"],
            "has_subtitles": has_subtitles,
            "allowFastPlayback": content.allow_fast_playback,
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

    context = {
        "content": content,
        "resource_file_key_id": resource_file_key.id if resource_file_key else None,
        "content_source_url": content_source_url,
        "youtube_video_id": youtube_video_id_for_content(content, content_source_url),
        "allow_events": True,
        "breadcrumbs": breadcrumb_trail(
            *content_parent_crumbs(content), (content.title, None)
        ),
    }

    return render(request, "core/player.html", context)


@require_GET
def stream_file(request, resource_file_key_id):
    """Stream file content with support for HTTP Range requests (partial content)."""
    try:
        # Keys are minted per playback by User.get_resource_filekey, which is where
        # the permission check happens. Binding the key to its user and expiring it
        # stops a key id -- sequential, and visible in the page source -- from being
        # a durable capability anyone can replay (#320).
        resource_file_key_obj = get_object_or_404(
            ResourceFileKey, id=resource_file_key_id
        )
        if not resource_file_key_obj.can_be_used_by(request.user):
            return forbidden("This media key is not valid for you, or has expired.")
        file_obj = resource_file_key_obj.resource_file

        # Check if file exists
        if not file_obj.file or not os.path.exists(file_obj.file.path):
            raise Http404("File not found")

        file_path = file_obj.file.path
        file_size = os.path.getsize(file_path)

        # Get proper MIME type
        content_type, _ = mimetypes.guess_type(file_path)
        if not content_type:
            if file_obj.file.name.lower().endswith((".mp4", ".m4v")):
                content_type = "video/mp4"
            elif file_obj.file.name.lower().endswith(".webm"):
                content_type = "video/webm"
            elif file_obj.file.name.lower().endswith((".mov", ".qt")):
                content_type = "video/quicktime"
            elif file_obj.file.name.lower().endswith(".mp3"):
                content_type = "audio/mpeg"
            elif file_obj.file.name.lower().endswith(".m4a"):
                content_type = "audio/mp4"
            elif file_obj.file.name.lower().endswith(".wav"):
                content_type = "audio/wav"
            else:
                content_type = "application/octet-stream"

        # Parse Range header
        range_header = request.META.get("HTTP_RANGE")
        if range_header:
            # Parse range header like "bytes=0-1023" or "bytes=1024-"
            range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if range_match:
                start = int(range_match.group(1))
                end = (
                    int(range_match.group(2)) if range_match.group(2) else file_size - 1
                )

                # Validate range - fix the validation logic
                if start >= file_size:
                    response = HttpResponse(status=416)  # Range Not Satisfiable
                    response["Content-Range"] = f"bytes */{file_size}"
                    return response

                # Ensure end doesn't exceed file size
                end = min(end, file_size - 1)

                # Read file chunk
                with open(file_path, "rb") as f:
                    f.seek(start)
                    chunk_size = end - start + 1
                    content = f.read(chunk_size)

                # Create partial content response
                response = HttpResponse(content, status=206)  # Partial Content
                response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                response["Content-Length"] = str(chunk_size)
                response["Accept-Ranges"] = "bytes"
                response["Content-Type"] = content_type

                # Add caching headers for better performance
                response["Cache-Control"] = "public, max-age=3600"
                response["ETag"] = f'"{file_size}-{os.path.getmtime(file_path)}"'

                return response

        # No range header - return full file (but WebKit will likely request ranges anyway)
        # For large files, consider always forcing range requests
        response = HttpResponse()
        response["Content-Length"] = str(file_size)
        response["Accept-Ranges"] = "bytes"
        response["Content-Type"] = content_type
        response["Cache-Control"] = "public, max-age=3600"
        response["ETag"] = f'"{file_size}-{os.path.getmtime(file_path)}"'

        # For large video files, encourage range requests
        if file_size > 1024 * 1024 and content_type.startswith("video/"):  # > 1MB
            # Return 206 with full range to encourage proper range handling
            response.status_code = 206
            response["Content-Range"] = f"bytes 0-{file_size - 1}/{file_size}"

        # Stream the content in chunks to avoid memory issues
        def file_iterator(file_path, chunk_size=8192):
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        response = HttpResponse(file_iterator(file_path), content_type=content_type)
        response["Content-Length"] = str(file_size)
        response["Accept-Ranges"] = "bytes"
        response["Cache-Control"] = "public, max-age=3600"
        response["ETag"] = f'"{file_size}-{os.path.getmtime(file_path)}"'

        return response

    except ResourceFileKey.DoesNotExist:
        raise Http404("Invalid resource file key")
    except Exception as e:
        return HttpResponse(f"Error streaming file: {str(e)}", status=500)


@require_GET
def playlists(request):
    owned_playlists = []
    if request.user.is_instructor:
        owned_playlists = [
            prepare_playlist_for_display(playlist)
            for playlist in Playlist.objects.filter(owner=request.user)
        ]

    # Course-derived access expires with the term, so only terms whose grace window
    # is still open can contribute a playlist the user could actually open.
    active = active_yearterms()

    # organize assigned playlists by yearterm and then by course.
    playlists_by_course_by_yearterm = []
    for yearterm in sorted(
        UserCourses.objects.filter(user=request.user, yearterm__in=active)
        .values_list("yearterm", flat=True)
        .distinct()
    ):
        user_courses = UserCourses.objects.filter(user=request.user, yearterm=yearterm)
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

    # Direct grants are listed whatever the role, but the same published/archived
    # filter applies, so a card is never shown for a playlist that would 403.
    manual_playlists = [
        prepare_playlist_for_display(playlist)
        for playlist in Playlist.objects.filter(
            playlistuseraccess__user=request.user, published=True, archived=False
        ).exclude(owner=request.user)
    ]

    context = {
        "user": request.user,
        "is_instructor": request.user.is_instructor,
        "owned_playlists": owned_playlists,
        "assigned_courses_by_yearterm": playlists_by_course_by_yearterm,
        "manual_playlists": manual_playlists,
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
            Playlist.objects.create(name=data["name"], owner=request.user)
            return HttpResponse()
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
        contents = Content.objects.filter(playlist=playlist)
        if not can_edit:
            contents = contents.filter(published=True)
        year_and_semester = get_semester_and_year_options()
        assigned_courses = get_assigned_courses(playlist, year_and_semester["yearterm"])

        return render(
            request,
            "core/playlist_info.html",
            {
                "playlist": playlist,
                "contents": contents,
                "can_edit": can_edit,
                "can_administer": playlist.can_be_administered_by(request.user),
                "form": PlaylistSettingsForm(instance=playlist),
                "year_options": year_and_semester["year_options"],
                "semester": year_and_semester["semester"],
                "assigned_courses": assigned_courses,
                "breadcrumbs": breadcrumb_trail((playlist.name, None)),
            },
        )
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
            playlist.published = form.cleaned_data["published"]
            playlist.archived = form.cleaned_data["archived"]
            playlist.save()
            return render_playlist_info(request, playlist)
        except Exception as e:
            logger.error(
                f"An error occured while attempting to update playlist settings. {e}"
            )
            return HttpResponseServerError()
    else:
        return HttpResponseBadRequest()


def accessible_resources(user):
    """The resource library as `user` may see it, matching User.can_access_resource."""
    if user.is_superuser or user.is_lab_assistant:
        return Resource.objects.all()
    granted = Q(resourceaccess__user=user)
    # A TA or co-instructor inherits every grant held by the owner of a playlist
    # they can write to.
    inherited = Q(
        resourceaccess__user__playlists_owned__playlistuseraccess__user=user,
        resourceaccess__user__playlists_owned__playlistuseraccess__playlist_role__in=(
            PlaylistRole.INSTRUCTOR,
            PlaylistRole.TA,
        ),
    )
    visible = granted | inherited
    if user.is_instructor:
        visible |= Q(checked_out_from_hbll=True) | Q(
            checked_out_from_other_byu_library=True
        )
    return Resource.objects.filter(visible).distinct()


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
        return HttpResponse()
    except Exception as e:
        logger.error(
            f"An error occured while deleting the playlist with id: {playlist.pk}. Exception: {e}"
        )
        return HttpResponseServerError()


# TODO (#335, #352): routed but unreferenced, and broken independently of permissions --
# create_content.html reverses `display_resources_files`, a URL name that does not exist,
# so this 500s on every request. The live path for adding content is
# display_create_from_resource. Delete this view, its template, and its route, or finish it.
@require_GET
@playlist_write_required
def display_create_content(request, playlist):
    return render(
        request,
        "core/partials/create_content.html",
        {
            "form": ContentForm(),
            "playlist": playlist,
            "resources": accessible_resources(request.user),
        },
    )


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
        Content.objects.create(
            playlist=playlist,
            title=parsed_data["title"],
            resource_file=resource_file,
        )
        return HttpResponse()
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
        Content.objects.create(
            playlist=playlist,
            title=parsed_data["title"],
            url=parsed_data["url"],
            resource=resource,
        )
        return HttpResponse()
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
def display_create_from_resource(request, playlist):
    try:
        return render(
            request,
            "core/create_from_resource.html",
            {
                "resources": accessible_resources(request.user),
                "playlist_id": playlist.pk,
                "breadcrumbs": breadcrumb_trail(
                    (playlist.name, reverse("playlist_info", args=[playlist.pk])),
                    ("Add from resource", None),
                ),
            },
        )
    except Exception as e:
        logger.error(
            f"Failed to display resources to create content from. Exception: {e}"
        )
        return HttpResponseServerError()


@require_POST
@playlist_write_required
def render_create_from_resource_form(request, playlist, resource_id):
    try:
        resource = get_object_or_404(Resource, pk=resource_id)
        if not request.user.can_access_resource(resource):
            return forbidden("You do not have access to that resource.")

        context = {
            "playlist_id": playlist.pk,
            "options": resource.resource_files.all(),
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
        if contents_count <= 1:
            return HttpResponse(
                "There is no published content for this playlist", status=200
            )
        else:
            return HttpResponse("", status=200)
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
            "can_edit": content.can_be_edited_by(request.user),
            "resource_file_key_id": resource_file_key.pk if resource_file_key else None,
            "content_source_url": content_source_url,
            "youtube_video_id": youtube_video_id_for_content(
                content, content_source_url
            ),
            "content_has_clips": content.has_clips(),
            "subtitle_options": content.get_subtitle_options(),
            "breadcrumbs": breadcrumb_trail(
                *content_parent_crumbs(content),
                (content.title, reverse("player", args=[content.pk])),
                ("Details", None),
            ),
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
            "subtitle_options": content.get_subtitle_options(),
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
        content.words = data["words"]
        content.allow_definitions = data["allow_definitions"]
        content.allow_notes = data["allow_notes"]
        content.allow_captions = data["allow_captions"]
        content.allow_fast_playback = data["allow_fast_playback"]
        content.clips_only = data["clips_only"]
        content.published = data["published"]

        default_subtitle_id = data.get("default_subtitle_track_id")
        if default_subtitle_id:
            resource = content.get_resource()
            content.default_subtitle_track = Subtitle.objects.filter(
                pk=default_subtitle_id, resource=resource
            ).first()
        else:
            content.default_subtitle_track = None

        content.save()
        return HttpResponse()
    except Exception as e:
        logger.error(f"An error occured while updating content. Exception: {e}")
        return HttpResponseServerError()


@require_POST
@content_write_required
def create_important_word(request, content):
    form = ImportantWordForm(request.POST)
    if form.is_valid():
        if not form.cleaned_data["word"]:
            return HttpResponseBadRequest()
        clean_word = form.cleaned_data["word"]
        # check if important word already exists
        already_exists = list(
            ImportantWord.objects.filter(content=content).filter(word=clean_word)
        )
        if already_exists:
            return HttpResponseBadRequest()
        important_word = ImportantWord.objects.create(
            content=content,
            word=form.cleaned_data["word"],
            translation=form.cleaned_data["translation"],
        )
        return render(
            request,
            "core/partials/important_word.html",
            {
                "word": {
                    "id": important_word.id,
                    "word": important_word.word,
                    "translation": important_word.translation,
                }
            },
        )
    else:
        return HttpResponseBadRequest()


@require_http_methods(["DELETE"])
@important_word_write_required
def delete_important_word(request, word):
    try:
        word.delete()
        return HttpResponse("", status=200)
    except Exception as e:
        logger.error(
            f"An error occured while deleting an important word. word_id: {word.pk}. Exception: {e}"
        )
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
    if not actor.is_admin:
        # Lab assistants may spoof any non-admin user, but never an admin.
        users = users.exclude(User.is_admin_q())
    users = users.order_by("last_name")[:25]
    html = render_to_string(
        "core/partials/spoof_user_options_for_select.html", {"users": users}
    )
    return HttpResponse(html)


# TODO (#335, #352): unrouted and unreferenced. Either delete it or route it behind
# @content_write_required -- as written it hands out a file key and full subtitle text
# for any content id.
@require_GET
def subtitle_editor(request, content_id):
    try:
        content = get_object_or_404(Content, id=content_id)
        resource = content.get_resource()
        subtitle_files = Subtitle.objects.filter(resource=resource)
        subtitle_options = []
        for sub_file in subtitle_files:
            subtitle_options.append(
                {
                    "name": sub_file.name,
                    "id": sub_file.pk,
                }
            )
        file_key = request.user.get_resource_filekey(content)
    except Exception as e:
        logger.error(
            f"Error retrieving subtitles for content_id: {content_id}. Exception: {e}"
        )
        return HttpResponseServerError()

    player_json = content.get_player_json()
    has_subtitles = bool(
        any(x.get("vtt") or x.get("url") for x in player_json["subtitleTracks"])
    )

    return render(
        request,
        "core/subtitle_editor.html",
        {
            "content": content,
            "subtitle_tracks": subtitle_options,
            "file_key": file_key.id if file_key else None,
            "content_source_url": request.user.get_content_source_url(content),
            "events": player_json["annotations"],
            "subtitles": player_json["subtitleTracks"],
            "has_subtitles": has_subtitles,
        },
    )


@require_http_methods(["GET", "POST"])
def request_resource(request):

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
        for field_name in form.errors:
            if field_name in form.fields:
                widget = form.fields[field_name].widget
                widget.attrs["class"] = (
                    f"{widget.attrs.get('class', '')} invalid-input".strip()
                )
    else:
        form = ResourceIntakeRequestForm()

    return render(
        request,
        "core/partials/resource_intake_request.html",
        {
            "form": form,
        },
    )


# TODO (#335, #352, #361): unrouted. Checks the instructor capability but not playlist
# ownership, so any instructor could TA themselves onto any playlist if it were wired up.
# Route it behind Playlist.can_grant_role, which enforces B1's rule that only the owner
# may grant TA or co-instructor. It also redirects to a `view_playlist` URL name that
# does not exist.
@require_POST
@playlist_write_required
def add_playlist_member(request, playlist):
    netid = request.POST.get("netid", "").strip()
    role = request.POST.get("role", "").strip()

    user = get_object_or_404(User, netid=netid)
    playlist_role = PlaylistRole.TA if role == "TA" else PlaylistRole.AUDITOR
    if not playlist.can_grant_role(request.user, playlist_role):
        return forbidden("Only the playlist owner can grant that role.")

    PlaylistUserAccess.objects.create(
        user=user,
        playlist=playlist,
        playlist_role=playlist_role,
    )

    return redirect("playlist_info", playlist_id=playlist.pk)
