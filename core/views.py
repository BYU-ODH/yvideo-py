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
from django.db import connection
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
from .models import BlankAnnotation
from .models import Content
from .models import Course
from .models import ImportantWord
from .models import MuteAnnotation
from .models import Playlist
from .models import PlaylistRole
from .models import PlaylistUserAccess
from .models import PrivilegeLevel
from .models import Resource
from .models import ResourceFile
from .models import ResourceFileKey
from .models import SkipAnnotation
from .models import Subtitle
from .models import User
from .models import UserCourses

logger = logging.getLogger(__name__)


def admin_or_superuser_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not getattr(request, "can_spoof", False):
            return redirect_to_login(
                request.get_full_path(), reverse("oidc_authentication_init")
            )
        return view_func(request, *args, **kwargs)

    return _wrapped


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


def index(request):
    if (
        request.user.is_authenticated
        and request.user.privilege_level == PrivilegeLevel.INSTRUCTOR
    ):
        return HttpResponseRedirect(reverse("playlists"))
    return render(request, "core/index.html", {})


@require_POST
def get_player_data(request, content_id):
    content = get_object_or_404(Content, id=content_id)
    try:
        player_json = content.get_player_json()
        has_subtitles = bool(
            any(x.get("vtt") or x.get("url") for x in player_json["subtitleTracks"])
        )

        data = {
            "annotations": player_json["annotations"],
            "subtitleTracks": player_json["subtitleTracks"],
            "has_subtitles": has_subtitles,
        }

        return JsonResponse(data)
    except Exception as e:
        logger.error(f"An error occurred while getting player data: {e}")
        return HttpResponseServerError()


# @login_required  # TODO: Uncomment
def player(request, content_id):
    """Render the video player page."""
    content = get_object_or_404(Content, id=content_id)
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
        "allow_events": True,
    }

    return render(request, "core/player.html", context)


def stream_file(request, resource_file_key_id):
    """Stream file content with support for HTTP Range requests (partial content)."""
    try:
        # Get the ResourceFileKey object
        resource_file_key_obj = get_object_or_404(
            ResourceFileKey, id=resource_file_key_id
        )
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


def playlists(request):
    # if admin, gather owned playlists
    owned_playlists = []
    if (
        request.user.privilege_level == PrivilegeLevel.INSTRUCTOR
        or request.user.privilege_level_override == PrivilegeLevel.INSTRUCTOR
        or request.user.is_admin
    ):
        owned_playlists_raw = Playlist.objects.filter(owner=request.user)
        owned_playlists = [
            prepare_playlist_for_display(playlist) for playlist in owned_playlists_raw
        ]

    # organize assigned playlists by yearterm and then by course.
    yearterms = []
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT yearterm FROM core_usercourses GROUP BY yearterm ORDER BY yearterm"""
        )
        yearterms = [result[0] for result in cursor.fetchall()]

    playlists_by_course_by_yearterm = []
    for yearterm in yearterms:
        user_courses = UserCourses.objects.filter(user=request.user, yearterm=yearterm)
        playlists_by_course = []
        for user_course in user_courses:
            playlists = Playlist.objects.filter(courses=user_course.course)
            playlists_by_course.append(
                {
                    "course_name": user_course.course.__str__(),
                    "playlists": [
                        prepare_playlist_for_display(playlist) for playlist in playlists
                    ],
                }
            )
        playlists_by_course_by_yearterm.append(
            {
                "yearterm_display": display_yearterm(yearterm),
                "playlists_by_course": playlists_by_course,
            }
        )

    manual_access_playlists = Playlist.objects.filter(
        playlistuseraccess__user=request.user
    )
    manual_playlists = []
    for playlist in manual_access_playlists:
        prepared_playlist = prepare_playlist_for_display(playlist)
        manual_playlists.append(prepared_playlist)

    context = {
        "user": request.user,
        "is_instructor": request.user.privilege_level == PrivilegeLevel.INSTRUCTOR,
        "owned_playlists": owned_playlists,
        "assigned_courses_by_yearterm": playlists_by_course_by_yearterm,
        "public_playlists": [],
        "manual_playlists": manual_playlists,
    }
    return render(request, "core/playlists.html", context)


def get_playlist_types(user):
    playlists = Playlist.objects.filter(owner=user)

    archived = playlists.filter(archived=True)
    published = playlists.filter(archived=False, published=True)
    unpublished = playlists.filter(archived=False, published=False)
    return {"archived": archived, "published": published, "unpublished": unpublished}


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


def playlist_info(request, playlist_id):
    try:
        playlist = Playlist.objects.get(pk=playlist_id)
        contents = Content.objects.filter(playlist=playlist)
        year_and_semester = get_semester_and_year_options()
        assigned_courses = get_assigned_courses(playlist, year_and_semester["yearterm"])

        return render(
            request,
            "core/playlist_info.html",
            {
                "playlist": playlist,
                "contents": contents,
                "form": PlaylistSettingsForm(instance=playlist),
                "year_options": year_and_semester["year_options"],
                "semester": year_and_semester["semester"],
                "assigned_courses": assigned_courses,
            },
        )
    except Playlist.DoesNotExist:
        logger.error(
            f"Failed to retrieve playlist info because playlist does not exist. Playlist ID: {playlist_id}"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to retrieve playlist info. Exception: {e}")
        return HttpResponseServerError()


def display_playlist_settings(request, playlist_id):
    try:
        playlist = Playlist.objects.get(pk=playlist_id)
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


def render_course_assignment(request):
    try:
        parsed_data = json.loads(request.body)
        playlist_id = parsed_data["playlist_id"]
        playlist = Playlist.objects.get(pk=playlist_id)
        semester = parsed_data["semester"]
        year = parsed_data["year"]

        # perform some minimal sanitation since we are passing this value into the db
        if len(semester) > 1 or len(year) > 4:
            return HttpResponseBadRequest()

        yearterm = f"{year}{semester}"
        assigned_courses = get_assigned_courses(playlist, yearterm)

        return render(
            request,
            "core/partials/course_assignment.html",
            {"assigned_courses": assigned_courses},
        )

    except Playlist.DoesNotExist:
        logger.error(
            "Failed to render course assignment information because the playlist does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to render course assignment information. Exception: {e}")
        return HttpResponseServerError()


def assign_playlist_to_course(request):
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
            or "playlist_id" not in parsed_data
        ):
            logger.error(
                "Failed to assign course to playlist because of insufficient data provided"
            )
            return HttpResponseBadRequest()
        playlist_id = parsed_data["playlist_id"]
        playlist = Playlist.objects.get(pk=playlist_id)

        dept = parsed_data["dept"]
        catalog_number = parsed_data["catalog_number"]
        section_numbers = parsed_data["sections"]
        yearterm = f"{parsed_data['year']}{parsed_data['semester']}"
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
        return render_course_assignment(request)

    except Playlist.DoesNotExist:
        logger.error(
            "Failed to assign course to playlist because the playlist does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to assign course to playlist. Exception: {e}")
        return HttpResponseServerError()


def update_playlist_course_sections(request):
    try:
        parsed_data = json.loads(request.body)
        if (
            "playlist_id" not in parsed_data
            or "sections" not in parsed_data
            or "dept" not in parsed_data
            or "catalog_number" not in parsed_data
            or "semester" not in parsed_data
            or "year" not in parsed_data
        ):
            logger.error(
                "Failed to update course sections because of insufficient data provided"
            )
            return HttpResponseBadRequest()
        playlist = Playlist.objects.get(pk=parsed_data["playlist_id"])
        new_sections_list = parsed_data["sections"]
        dept = parsed_data["dept"]
        catalog_number = parsed_data["catalog_number"]
        yearterm = f"{parsed_data['year']}{parsed_data['semester']}"
        courses = Course.objects.filter(
            dept=dept, catalog_number=catalog_number, yearterm=yearterm
        )

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

    except Playlist.DoesNotExist:
        logger.error(
            "Failed to update course sections because the playlist does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to update course sections. Exception: {e}")
        return HttpResponseServerError()


def unassign_playlist_from_course(request):
    try:
        parsed_data = json.loads(request.body)
        if (
            "dept" not in parsed_data
            or "catalog_number" not in parsed_data
            or "semester" not in parsed_data
            or "year" not in parsed_data
            or "playlist_id" not in parsed_data
        ):
            logger.error(
                "Failed to remove playlist from course because of insufficient data"
            )
            return HttpResponseBadRequest()

        playlist = Playlist.objects.get(pk=parsed_data["playlist_id"])
        courses = playlist.courses.all().filter(
            dept=parsed_data["dept"],
            catalog_number=parsed_data["catalog_number"],
            yearterm=f"{parsed_data['year']}{parsed_data['semester']}",
        )
        playlist.courses.remove(*courses)
        return HttpResponse()

    except Playlist.DoesNotExist:
        logger.error(
            "Failed to remove playlist assigned to course because the playlist does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to remove playlist assigned to course. Exception: {e}")
        return HttpResponseServerError()


@require_POST
def update_playlist_settings(request):
    form = PlaylistSettingsForm(request.POST)
    if form.is_valid():
        try:
            playlist = Playlist.objects.get(pk=form.cleaned_data["id"])
            playlist.name = form.cleaned_data["name"]
            playlist.published = form.cleaned_data["published"]
            playlist.archived = form.cleaned_data["archived"]
            playlist.save()
            return playlist_info(request, playlist.id)
        except Exception as e:
            logger.error(
                f"An error occured while attempting to update playlist settings. {e}"
            )
            return HttpResponseServerError()
    else:
        return HttpResponseBadRequest()


def get_playlist_contents(playlist):
    contents = Content.objects.filter(playlist=playlist)
    published = contents.filter(published=True)
    unpublished = contents.filter(published=False)
    return {"published": published, "unpublished": unpublished}


@require_http_methods(["DELETE"])
def delete_playlist(request, playlist_id):
    try:
        playlist = Playlist.objects.get(pk=playlist_id)
        if request.user.is_admin or request.user == playlist.owner:
            playlist.delete()
        return HttpResponse()
    except Exception as e:
        logger.error(
            f"An error occured while deleting the playlist with id: {playlist_id}. Exception: {e}"
        )
        return HttpResponseServerError()


@require_GET
def display_create_content(request, playlist_id):
    form = ContentForm()
    playlist = get_object_or_404(Playlist, pk=playlist_id)
    resources = Resource.objects.all()
    return render(
        request,
        "core/partials/create_content.html",
        {
            "form": form,
            "playlist": playlist,
            "resources": resources,
        },
    )


@require_POST
def create_content(request):
    try:
        parsed_data = json.loads(request.body)
        if (
            "playlist_id" not in parsed_data
            or "title" not in parsed_data
            or "resource_file_id" not in parsed_data
        ):
            logger.error(
                "Failed to create new content because of invalid data provided."
            )
            return HttpResponseBadRequest()

        playlist = Playlist.objects.get(pk=parsed_data["playlist_id"])
        resource_file = ResourceFile.objects.get(pk=parsed_data["resource_file_id"])
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


def display_create_from_resource(request, playlist_id):
    if playlist_id is None:
        return HttpResponseBadRequest()
    try:
        Playlist.objects.get(pk=playlist_id)
    except Exception as e:
        logger.error(
            f"Failed to display resources to create a content for the given playlist. Exception: {e}"
        )
        return HttpResponseBadRequest()

    try:
        resources = Resource.objects.all()
        return render(
            request,
            "core/create_from_resource.html",
            {"resources": resources, "playlist_id": playlist_id},
        )
    except Exception as e:
        logger.error(
            f"Failed to display resources to create content from. Exception: {e}"
        )
        return HttpResponseServerError()


def render_create_from_resource_form(request):
    try:
        parsed_data = json.loads(request.body)
        if "resource_id" not in parsed_data or "playlist_id" not in parsed_data:
            return HttpResponseBadRequest()

        resource_id = parsed_data["resource_id"]
        resource = Resource.objects.get(pk=resource_id)

        context = {
            "playlist_id": parsed_data["playlist_id"],
            "options": resource.resource_files.all(),
        }
        return render(request, "core/partials/create_from_resource_form.html", context)
    except Resource.DoesNotExist:
        logger.error(
            "Failed to render the create from resource form because no Resource matches the provided resource_id"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to render the create from resource form. Exception: {e}")
        return HttpResponseServerError()


@require_http_methods(["DELETE"])
def delete_content(request, content_id):
    content = get_object_or_404(Content, pk=content_id)
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
            f"An error occured while deleting content with id: {content_id}. Exception: {e}"
        )
        return HttpResponseServerError()
    return HttpResponseBadRequest()


def display_content_info(request, content_id):
    try:
        content = Content.objects.get(pk=content_id)
        resource_file_key = request.user.get_resource_filekey(content)
        context = {
            "content": content,
            "content_id": content.pk,
            "resource_file_key_id": resource_file_key.pk,
        }
        return render(request, "core/content_info.html", context)
    except Content.DoesNotExist:
        logger.error(
            f"Failed to display content settings because of missing content object. Id provided: {content_id}"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to display content settings. Exception: {e}")
        return HttpResponseServerError()


def render_content_settings_form(request, content_id):
    try:
        content = Content.objects.get(pk=content_id)
        context = {"content": content}
        return render(request, "core/partials/content_settings_form.html", context)
    except Content.DoesNotExist:
        logger.error(
            "Failed to render content settings form beacuse the requested content does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to render content settings form. Exception: {e}")
        return HttpResponseServerError()


def remove_content_from_playlist(request, content_id):
    try:
        content = Content.objects.get(pk=content_id)
        if request.user.is_admin or request.user == content.playlist.owner:
            playlist_id = content.playlist.pk
            content.playlist = None
            content.save()
            return HttpResponse(playlist_id)
        else:
            return HttpResponse(status=401)

    except Content.DoesNotExist:
        logger.error(
            "Failed to remove content from playlist because the content doesn't exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to remove content from playlist. Exception: {e}")
        return HttpResponseServerError()


@require_POST
def update_content(request):
    try:
        data = json.loads(request.body)
        content = Content.objects.get(pk=data["id"])
        content.title = data["title"]
        content.description = data["description"]
        content.words = data["words"]
        content.allow_definitions = data["allow_definitions"]
        content.allow_notes = data["allow_notes"]
        content.allow_captions = data["allow_captions"]
        content.published = data["published"]

        content.save()
        return HttpResponse()
    except Content.DoesNotExist:
        logger.error(
            "Failed to update content because the content object does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"An error occured while updating content. Exception: {e}")
        return HttpResponseServerError()


@require_POST
def create_important_word(request):
    content = get_object_or_404(Content, pk=request.POST["content_id"])
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
def delete_important_word(request, word_id):
    word = get_object_or_404(ImportantWord, pk=word_id)
    try:
        word.delete()
        return HttpResponse("", status=200)
    except Exception as e:
        logger.error(
            f"An error occured while deleting an important word. word_id: {word_id}. Exception: {e}"
        )
        return HttpResponseServerError()


def add_annotation(request, content_id, annotation_type):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    # Map strings to model classes
    annotation_types = {
        "skip": SkipAnnotation,
        "mute": MuteAnnotation,
        "blank": BlankAnnotation,
    }

    annotation_class = annotation_types.get(annotation_type.lower())

    content_obj = get_object_or_404(Content, id=content_id)

    # Get POST data
    annotation_id = request.POST.get("annotation_id")
    name = request.POST.get("name", "")
    owner = request.POST.get("owner", "")  # Change to request.user when auth is set up
    start_time = float(request.POST.get("start_time", 0))
    end_time = float(request.POST.get("end_time", 0))
    description = request.POST.get("description", "")

    if annotation_id:
        # Update existing annotation
        annotation = get_object_or_404(annotation_class, id=annotation_id)
        annotation.name = name
        annotation.start_time = start_time
        annotation.end_time = end_time
        annotation.description = description
        annotation.save()
    else:
        # Create new annotation
        annotation = annotation_class.objects.create(
            content=content_obj,
            owner=owner,
            name=name,
            start_time=start_time,
            end_time=end_time,
            description=description,
        )

    # Return JSON response
    return JsonResponse(
        {
            "id": annotation.id,
            "name": annotation.name,
            "type": annotation.annotation_type,
            "owner": annotation.owner.username,
            "start_time": annotation.start_time,
            "end_time": annotation.end_time,
            "description": annotation.description,
        }
    )


@login_not_required
def invalid_login(request):
    return render(request, "core/invalid_login.html", {})


@admin_or_superuser_required
def spoof_user_start(request):
    if request.method == "POST":
        spoof_user_id = request.POST.get("spoof_user_id")
        if spoof_user_id:
            request.session["spoof_user_id"] = int(spoof_user_id)
        return redirect(
            request.POST.get("next") or request.headers.get("Referer") or "/"
        )
    return redirect("/")


@admin_or_superuser_required
def spoof_user_stop(request):
    request.session.pop("spoof_user_id", None)
    return redirect(request.GET.get("next") or request.headers.get("Referer") or "/")


@admin_or_superuser_required
def spoof_user_search(request):
    if request.method != "POST":
        return HttpResponse(status=405)
    query = request.POST.get("search", "").strip()
    users = User.objects.filter(
        (
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(username__icontains=query)
        )
        & ~Q(id=request.user.id)
    ).order_by("last_name")[:25]
    html = render_to_string(
        "core/partials/spoof_user_options_for_select.html", {"users": users}
    )
    return HttpResponse(html)


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


def add_playlist_member(request, playlist_id):
    if not (
        request.user.privilege_level == PrivilegeLevel.INSTRUCTOR
        or request.user.is_admin
    ):
        return HttpResponse("Forbidden", status=403)

    playlist = get_object_or_404(Playlist, pk=playlist_id)

    if request.method == "POST":
        # Get form data
        netid = request.POST.get("netid", "").strip()
        role = request.POST.get("role", "").strip()

        user = get_object_or_404(User, netid=netid)

        if role == "TA":
            playlist_role = PlaylistRole.TA
        else:
            playlist_role = PlaylistRole.AUDITOR

        PlaylistUserAccess.objects.create(
            user=user,
            playlist=playlist,
            playlist_role=playlist_role,
        )

        return redirect("view_playlist", pk=playlist.id)

    return HttpResponseBadRequest()
