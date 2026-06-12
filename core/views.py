from datetime import datetime
from functools import wraps
import json
import logging
import mimetypes
import os
import re

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

from .forms import ClipForm
from .forms import CollectionSettingsForm
from .forms import ContentForm
from .forms import ImportantWordForm
from .forms import ResourceContentIntakeRequestForm
from .forms import UpdateContentForm
from .models import BlankAnnotation
from .models import Clip
from .models import Collection
from .models import CollectionRole
from .models import CollectionUserAccess
from .models import Content
from .models import Course
from .models import ImportantWord
from .models import MuteAnnotation
from .models import Resource
from .models import ResourceFile
from .models import ResourceFileKey
from .models import SkipAnnotation
from .models import Subtitle
from .models import User
from .models import UserCourses
from .utils import hms2seconds
from .utils import seconds2hms

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


def prepare_collection_for_display(collection):
    published_contents = Content.objects.filter(collection=collection).filter(
        published=True
    )
    contents_count = published_contents.count()
    parsed_collection = {
        "pk": collection.pk,
        "name": collection.name,
        "items_display": f"{contents_count} items"
        if contents_count > 1 or contents_count == 0
        else f"{contents_count} item",
        "published_contents": published_contents,
    }
    return parsed_collection


def display_yearterm(yearterm):
    term_decoder = {"1": "Winter", "3": "Spring", "4": "Summer", "5": "Fall"}
    year_string = yearterm[0:4]
    term_string = yearterm[4:]
    return f"{term_decoder[term_string]} {year_string}"


def index(request):
    if request.user.is_authenticated and request.user.privilege_level == 2:
        return HttpResponseRedirect(reverse("collections"))
    return render(request, "index.html", {})


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
            "clips": player_json["clips"],
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
    if not resource_file_key:
        return HttpResponse(
            "User does not have permission to view this content", status=403
        )

    context = {
        "content": content,
        "resource_file_key_id": resource_file_key.id if resource_file_key else None,
        "allow_events": True,
    }

    return render(request, "player.html", context)


def stream_file(request, resource_file_key_id):
    """Stream file content with support for HTTP Range requests (partial content)."""
    try:
        # Get the FileKey object
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


def collections(request):
    # if admin, gather owned collections
    owned_collections = []
    allowed_privilege_levels = [2, 0]
    if (
        request.user.privilege_level in allowed_privilege_levels
        or request.user.privilege_level_override in allowed_privilege_levels
    ):
        owned_collections_raw = Collection.objects.filter(owner=request.user)
        owned_collections = [
            prepare_collection_for_display(collection)
            for collection in owned_collections_raw
        ]

    # organize assigned collections by yearterm and then by course.
    yearterms = []
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT yearterm FROM core_usercourses GROUP BY yearterm ORDER BY yearterm"""
        )
        yearterms = [result[0] for result in cursor.fetchall()]

    collections_by_course_by_yearterm = []
    for yearterm in yearterms:
        user_courses = UserCourses.objects.filter(user=request.user, yearterm=yearterm)
        collections_by_course = []
        for user_course in user_courses:
            collections = Collection.objects.filter(courses=user_course.course)
            collections_by_course.append(
                {
                    "course_name": user_course.course.__str__(),
                    "collections": [
                        prepare_collection_for_display(collection)
                        for collection in collections
                    ],
                }
            )
        collections_by_course_by_yearterm.append(
            {
                "yearterm_display": display_yearterm(yearterm),
                "collections_by_course": collections_by_course,
            }
        )

    manual_access_collections = Collection.objects.filter(
        collectionuseraccess__user=request.user
    )
    manual_collections = []
    for collection in manual_access_collections:
        prepared_collection = prepare_collection_for_display(collection)
        manual_collections.append(prepared_collection)

    context = {
        "user": request.user,
        "owned_collections": owned_collections,
        "assigned_courses_by_yearterm": collections_by_course_by_yearterm,
        "public_collections": [],
        "manual_collections": manual_collections,
    }
    return render(request, "collections.html", context)


def get_collection_types(user):
    collections = Collection.objects.filter(owner=user)

    archived = collections.filter(archived=True)
    published = collections.filter(archived=False, published=True)
    unpublished = collections.filter(archived=False, published=False)
    return {"archived": archived, "published": published, "unpublished": unpublished}


def create_collection(request):
    data = json.loads(request.body)
    if data and "name" in data:
        try:
            Collection.objects.create(name=data["name"], owner=request.user)
            return HttpResponse()
        except Exception as e:
            logger.error(
                f"An error occured when the user: {request.user} attempted to create a collection. Exception: {e}"
            )

            return HttpResponseServerError()

    else:
        return HttpResponseBadRequest()


def get_semester_and_year_options():
    # we need a list of years for the year selector when assigning collection to course
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


def get_assigned_courses(collection, yearterm):
    courses = collection.courses.filter(yearterm=yearterm)
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


def collection_info(request, collection_id):
    try:
        collection = Collection.objects.get(pk=collection_id)
        contents = Content.objects.filter(collection=collection)
        year_and_semester = get_semester_and_year_options()
        assigned_courses = get_assigned_courses(
            collection, year_and_semester["yearterm"]
        )

        return render(
            request,
            "collection_info.html",
            {
                "collection": collection,
                "contents": contents,
                "form": CollectionSettingsForm(instance=collection),
                "year_options": year_and_semester["year_options"],
                "semester": year_and_semester["semester"],
                "assigned_courses": assigned_courses,
            },
        )
    except Collection.DoesNotExist:
        logger.error(
            f"Failed to retrieve collection info because collection does not exist. Collection ID: {collection_id}"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to retrieve collection info. Exception: {e}")
        return HttpResponseServerError()


def display_collection_settings(request, collection_id):
    try:
        collection = Collection.objects.get(pk=collection_id)
        form = CollectionSettingsForm(instance=collection)
        year_and_semester = get_semester_and_year_options()

        context = {
            "collection": collection,
            "form": form,
            "semester": year_and_semester["semester"],
            "year_options": year_and_semester["year_options"],
        }
        return render(request, "partials/collection_settings.html", context)
    except Exception as e:
        logger.error(f"Failed to render collection settings. Exception: {e}")
        return HttpResponseServerError()


def render_course_assignment(request):
    try:
        parsed_data = json.loads(request.body)
        collection_id = parsed_data["collection_id"]
        collection = Collection.objects.get(pk=collection_id)
        semester = parsed_data["semester"]
        year = parsed_data["year"]

        # perform some minimal sanitation since we are passing this value into the db
        if len(semester) > 1 or len(year) > 4:
            return HttpResponseBadRequest()

        yearterm = f"{year}{semester}"
        assigned_courses = get_assigned_courses(collection, yearterm)

        return render(
            request,
            "partials/course_assignment.html",
            {"assigned_courses": assigned_courses},
        )

    except Collection.DoesNotExist:
        logger.error(
            "Failed to render course assignment information because the collection does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to render course assignment information. Exception: {e}")
        return HttpResponseServerError()


def assign_collection_to_course(request):
    # first check if the course already exists. if so, add the collection to that course.
    # otherwise, create the course and then add the collection
    try:
        parsed_data = json.loads(request.body)
        if (
            "dept" not in parsed_data
            or "catalog_number" not in parsed_data
            or "sections" not in parsed_data
            or "year" not in parsed_data
            or "semester" not in parsed_data
            or "collection_id" not in parsed_data
        ):
            logger.error(
                "Failed to assign course to collection because of insufficient data provided"
            )
            return HttpResponseBadRequest()
        collection_id = parsed_data["collection_id"]
        collection = Collection.objects.get(pk=collection_id)

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
                        f"More than one course was returned when assigning a collection ({collection}) to a course. Assigning to the first result"
                    )
                existing_course = existing_course_filter.first()
            collection.courses.add(existing_course)
            collection.save()
        return render_course_assignment(request)

    except Collection.DoesNotExist:
        logger.error(
            "Failed to assign course to collection because the collection does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to assign course to collection. Exception: {e}")
        return HttpResponseServerError()


def update_collection_course_sections(request):
    try:
        parsed_data = json.loads(request.body)
        if (
            "collection_id" not in parsed_data
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
        collection = Collection.objects.get(pk=parsed_data["collection_id"])
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

        # Associate the provided sections with the collection.
        # We could go through and figure out exactly which should be removed and which should be added,
        # but it is probably more robust to simply remove all associations for this dept, catalog_number, and yearterm
        # and then add all the sections provided by the user.
        collection.courses.remove(*courses)
        new_courses = Course.objects.filter(
            dept=dept,
            catalog_number=catalog_number,
            yearterm=yearterm,
            section_number__in=new_sections_list,
        )
        collection.courses.add(*new_courses)

        return HttpResponse()

    except Collection.DoesNotExist:
        logger.error(
            "Failed to update course sections because the collection does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to update course sections. Exception: {e}")
        return HttpResponseServerError()


def unassign_collection_from_course(request):
    try:
        parsed_data = json.loads(request.body)
        if (
            "dept" not in parsed_data
            or "catalog_number" not in parsed_data
            or "semester" not in parsed_data
            or "year" not in parsed_data
            or "collection_id" not in parsed_data
        ):
            logger.error(
                "Failed to remove collection from course because of insufficient data"
            )
            return HttpResponseBadRequest()

        collection = Collection.objects.get(pk=parsed_data["collection_id"])
        courses = collection.courses.all().filter(
            dept=parsed_data["dept"],
            catalog_number=parsed_data["catalog_number"],
            yearterm=f"{parsed_data['year']}{parsed_data['semester']}",
        )
        collection.courses.remove(*courses)
        return HttpResponse()

    except Collection.DoesNotExist:
        logger.error(
            "Failed to remove collection assigned to course because the collection does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to remove collection assigned to course. Exception: {e}")
        return HttpResponseServerError()


@require_POST
def update_collection_settings(request):
    form = CollectionSettingsForm(request.POST)
    if form.is_valid():
        try:
            collection = Collection.objects.get(pk=form.cleaned_data["id"])
            collection.name = form.cleaned_data["name"]
            collection.published = form.cleaned_data["published"]
            collection.archived = form.cleaned_data["archived"]
            collection.save()
            collection_types = get_collection_types(request.user)
            context = {
                "collection": collection,
                "published": collection_types["published"],
                "unpublished": collection_types["unpublished"],
                "archived": collection_types["archived"],
            }
            return collection_info(request, collection.id)
        except Exception as e:
            logger.error(
                f"An error occured while attempting to update collection settings. {e}"
            )
            return HttpResponseServerError()
    else:
        return HttpResponseBadRequest()


def get_collection_contents(collection):
    contents = Content.objects.filter(collection=collection)
    published = contents.filter(published=True)
    unpublished = contents.filter(published=False)
    return {"published": published, "unpublished": unpublished}


@require_http_methods(["DELETE"])
def delete_collection(request, collection_id):
    try:
        collection = Collection.objects.get(pk=collection_id)
        if request.user.is_admin or request.user == collection.owner:
            collection.delete()
        return HttpResponse()
    except Exception as e:
        logger.error(
            f"An error occured while deleting the collection with id: {collection_id}. Exception: {e}"
        )
        return HttpResponseServerError()


@require_GET
def display_create_content(request, collection_id):
    form = ContentForm()
    collection = get_object_or_404(Collection, pk=collection_id)
    resources = Resource.objects.all()
    return render(
        request,
        "partials/create_content.html",
        {
            "form": form,
            "collection": collection,
            "resources": resources,
        },
    )


@require_POST
def create_content(request):
    try:
        parsed_data = json.loads(request.body)
        if (
            "collection_id" not in parsed_data
            or "title" not in parsed_data
            or "resource_file_id" not in parsed_data
        ):
            logger.error(
                f"Failed to create new content beacuse of invalid data provided. Exception: {e}"
            )
            return HttpResponseBadRequest()

        collection = Collection.objects.get(pk=parsed_data["collection_id"])
        resource_file = ResourceFile.objects.get(pk=parsed_data["resource_file_id"])
        Content.objects.create(
            collection=collection,
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


def display_create_from_resource(request, collection_id):
    if collection_id is None:
        return HttpResponseBadRequest()
    try:
        Collection.objects.get(pk=collection_id)
    except Exception as e:
        logger.error(
            f"Failed to display resources to create a content for the given collection. Exception: {e}"
        )
        return HttpResponseBadRequest()

    try:
        resources = Resource.objects.all()
        return render(
            request,
            "create_from_resource.html",
            {"resources": resources, "collection_id": collection_id},
        )
    except Exception as e:
        logger.error(
            f"Failed to display resources to create content from. Exception: {e}"
        )
        return HttpResponseServerError()


def render_create_from_resource_form(request):
    try:
        parsed_data = json.loads(request.body)
        if "resource_id" not in parsed_data or "collection_id" not in parsed_data:
            return HttpResponseBadRequest()

        resource_id = parsed_data["resource_id"]
        resource = Resource.objects.get(pk=resource_id)

        context = {
            "collection_id": parsed_data["collection_id"],
            "options": resource.resource_files.all(),
        }
        return render(request, "partials/create_from_resource_form.html", context)
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
        contents_count = Content.objects.filter(collection=content.collection).count()
        content.delete()
        if contents_count <= 1:
            return HttpResponse(
                "There is no published content for this collection", status=200
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
        form = UpdateContentForm(instance=content)
        word_form = ImportantWordForm()
        words = ImportantWord.objects.filter(content=content)
        context = {
            "content": content,
            "content_id": content.pk,
            "resource_file_key_id": resource_file_key.pk,
        }
        return render(request, "content_info.html", context)
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
        return render(request, "partials/content_settings_form.html", context)
    except Content.DoesNotExist:
        logger.error(
            "Failed to render content settings form beacuse the requested content does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to render content settings form. Exception: {e}")
        return HttpResponseServerError()


def remove_content_from_collection(request, content_id):
    try:
        content = Content.objects.get(pk=content_id)
        if request.user.is_admin or request.user == content.collection.owner:
            collection_id = content.collection.pk
            content.collection = None
            content.save()
            return HttpResponse(collection_id)
        else:
            return HttpResponse(status=401)

    except Content.DoesNotExist:
        logger.error(
            "Failed to remove content from collection because the content doesn't exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to remove content from collection. Exception: {e}")
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
        context = {"content": content}
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
            "partials/important_word.html",
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
    return render(request, "invalid_login.html", {})


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
        "partials/spoof_user_options_for_select.html", {"users": users}
    )
    return HttpResponse(html)


# TODO add permission check
def clip_editor(request, content_id):
    """Render the clip editor page."""
    content = get_object_or_404(Content, id=content_id)
    file_key = request.user.get_filekey(content)

    # Calculate clip positions
    duration = content.duration
    clips_with_positions = []
    clips_json = []

    for clip in content.clips.all():
        start_time = hms2seconds(clip.start_time)
        end_time = hms2seconds(clip.end_time)
        start_percent = (start_time / duration * 100) if duration > 0 else 0
        width_percent = (
            ((end_time - start_time) / duration * 100) if duration > 0 else 0
        )

        clips_with_positions.append(
            {
                "clip": clip,
                "content": content,  # Add content to each item context
                "left": f"{start_percent:.2f}%",
                "width": f"{width_percent:.2f}%",
                "start": start_time,
                "end": end_time,
            }
        )
        clips_json.append(
            {
                "start": start_time,
                "end": end_time,
                "label": clip.name,
            }
        )
    clips_json = json.dumps(clips_json)
    # Prepare subtitle data in the format expected by AnnotationPlayer
    subtitles_data = []

    has_subtitles = bool(any(x.get("vtt") or x.get("url") for x in subtitles_data))

    context = {
        "content": content,
        "file_key": file_key.id if file_key else None,
        "allow_events": True,
        "events": json.dumps([]),
        "subtitles": json.dumps(subtitles_data),
        "clips_json": clips_json,
        "has_subtitles": has_subtitles,
        "duration": duration,
        "clips_with_positions": clips_with_positions,
    }

    return render(request, "clip_editor.html", context)


def generate_clips_json_data(content):
    """Generate clips JSON data for AnnotationPlayer."""
    clips = []
    for clip in content.clips.all():
        clips.append(
            {
                "start": hms2seconds(clip.start_time),
                "end": hms2seconds(clip.end_time),
                "label": clip.name,
            }
        )
    return clips


@require_GET
def load_clip_form(request, clip_id):
    """Load clip editing form via HTMX."""
    clip = get_object_or_404(Clip, id=clip_id)

    # Get content from query parameter (required for context)
    content_id = request.GET.get("content_id")
    if not content_id:
        return HttpResponse("Missing content_id parameter", status=400)

    content = get_object_or_404(Content, id=content_id)

    # Check permissions on the content
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    # Check if user can edit this clip
    can_edit = clip.can_edit(request.user)

    form = ClipForm(instance=clip)

    context = {
        "clip": clip,
        "content": content,
        "can_edit": can_edit,
        "form": form,
        "start_seconds": hms2seconds(clip.start_time),
        "end_seconds": hms2seconds(clip.end_time),
    }

    return render(request, "partials/clip_form.html", context)


@require_POST
def update_clip(request, clip_id):
    """Update clip and return updated HTML with JSON OOB."""
    clip = get_object_or_404(Clip, id=clip_id)

    # Get content from POST data (required for context)
    content_id = request.POST.get("content_id")
    if not content_id:
        return HttpResponse("Missing content_id", status=400)

    content = get_object_or_404(Content, id=content_id)

    # Check permissions on the content
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    # If user doesn't own this clip, clone it
    if not clip.can_edit(request.user):
        original_clip = clip
        clip = clip.clone_for_user(request.user)

        # Add the new clip to the content (replacing the original)
        content.clips.remove(original_clip)
        content.clips.add(clip)
        content.save()

    # Check if this is a delta-based update (from drag/resize)
    delta_left = request.POST.get("delta_left")
    delta_width = request.POST.get("delta_width")

    if delta_left is not None or delta_width is not None:
        # Delta-based update from drag/resize
        duration = content.duration

        # Get current position percentages
        start_time = hms2seconds(clip.start_time)
        end_time = hms2seconds(clip.end_time)
        current_left = (start_time / duration * 100) if duration > 0 else 0
        current_width = (
            ((end_time - start_time) / duration * 100) if duration > 0 else 0
        )

        # Apply deltas
        delta_left_float = float(delta_left) if delta_left else 0
        delta_width_float = float(delta_width) if delta_width else 0

        new_left = current_left + delta_left_float
        new_width = current_width + delta_width_float

        # Convert back to times
        new_start = (new_left / 100) * duration
        new_end = ((new_left + new_width) / 100) * duration

        # Validate
        if new_start < 0 or new_end > duration or new_start >= new_end:
            # Return error - keep original position
            position = {
                "left": f"{current_left:.2f}%",
                "width": f"{current_width:.2f}%",
                "start": start_time,
                "end": end_time,
            }

            context = {
                "clip": clip,
                "content": content,
                "position": position,
                "error": "Invalid clip position",
            }
            return render(request, "partials/clip_item.html", context)

        # Update clip with new times
        clip.start_time = seconds2hms(new_start)
        clip.end_time = seconds2hms(new_end)

        # Update name if provided
        name = request.POST.get("name")
        if name:
            clip.name = name

        clip.save()
    else:
        # Form-based update
        form = ClipForm(request.POST, instance=clip)

        if not form.is_valid():
            # Return form with errors
            context = {
                "clip": clip,
                "content": content,
                "can_edit": True,
                "form": form,
                "start_seconds": hms2seconds(clip.start_time),
                "end_seconds": hms2seconds(clip.end_time),
            }
            return render(request, "partials/clip_form.html", context)

        clip = form.save()

    # Calculate new position
    duration = content.duration
    start_time = hms2seconds(clip.start_time)
    end_time = hms2seconds(clip.end_time)
    start_percent = (start_time / duration * 100) if duration > 0 else 0
    width_percent = ((end_time - start_time) / duration * 100) if duration > 0 else 0

    position = {
        "left": f"{start_percent:.2f}%",
        "width": f"{width_percent:.2f}%",
        "start": start_time,
        "end": end_time,
    }

    # Generate updated JSON for player - use the content being edited
    clips_json_data = generate_clips_json_data(content)

    # Render the layer item
    item_html = render_to_string(
        "partials/clip_item.html",
        {
            "clip": clip,
            "content": content,  # Add content to context
            "position": position,
        },
    )

    # Always render the updated form with OOB swap
    form_content = render_to_string(
        "partials/clip_form.html",
        {
            "clip": clip,
            "content": content,
            "can_edit": True,
            "form": ClipForm(instance=clip),
            "start_seconds": start_time,
            "end_seconds": end_time,
        },
    )
    # Wrap with OOB swap directive
    form_html = f'<div hx-swap-oob="innerHTML:#detail-form">{form_content}</div>'

    # Render JSON OOB update
    json_html = render_to_string(
        "partials/clips_json_oob.html",
        {
            "clips_json": json.dumps(clips_json_data),
        },
    )

    # Combine responses
    response = HttpResponse(item_html + form_html + json_html)
    return response


@require_POST
def create_clip(request, content_id):
    """Create a new clip and return updated HTML with JSON OOB."""
    content = get_object_or_404(Content, id=content_id)

    # Check permissions
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    # Get start and end times from POST
    start_time = float(request.POST.get("start_time", 0))
    end_time = float(request.POST.get("end_time", 10))

    # Validate times
    duration = content.duration
    if start_time < 0 or end_time > duration or start_time >= end_time:
        return HttpResponse("Invalid clip times", status=400)

    # Get resource from content's file
    if not content.file or not content.file.resource:
        return HttpResponse("Content has no associated resource", status=400)

    # Create new clip
    clip = Clip.objects.create(
        resource=content.file.resource,
        owner=request.user,
        name=f"Clip {content.clips.count() + 1}",
        start_time=seconds2hms(start_time),
        end_time=seconds2hms(end_time),
    )

    # Add to content
    content.clips.add(clip)
    content.save()

    # Calculate position
    start_percent = (start_time / duration * 100) if duration > 0 else 0
    width_percent = ((end_time - start_time) / duration * 100) if duration > 0 else 0

    position = {
        "left": f"{start_percent:.2f}%",
        "width": f"{width_percent:.2f}%",
        "start": start_time,
        "end": end_time,
    }

    # Generate updated JSON for player
    clips_json_data = generate_clips_json_data(content)

    # Render the new layer item
    item_html = render_to_string(
        "partials/clip_item.html",
        {
            "clip": clip,
            "content": content,
            "position": position,
        },
    )

    # Render JSON OOB update
    json_html = render_to_string(
        "partials/clips_json_oob.html",
        {
            "clips_json": json.dumps(clips_json_data),
        },
    )

    # Combine responses with OOB swap for layer-items
    response = HttpResponse(
        f'<div hx-swap-oob="beforeend:.layer-items">{item_html}</div>{json_html}'
    )
    return response


@require_http_methods(["DELETE"])
def delete_clip(request, clip_id):
    """Delete or remove clip from content and return updated JSON OOB."""
    clip = get_object_or_404(Clip, id=clip_id)

    # Get content from query/body parameter
    content_id = request.GET.get("content_id") or request.POST.get("content_id")
    if not content_id:
        return HttpResponse("Missing content_id parameter", status=400)

    content = get_object_or_404(Content, id=content_id)

    # Check permissions
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    try:
        if clip.can_edit(request.user):
            # User owns the clip, can fully delete it
            clip.delete()
        else:
            # User doesn't own it, just remove from their content
            content.clips.remove(clip)
            content.save()

        # Generate updated JSON for player
        clips_json_data = generate_clips_json_data(content)

        # Render JSON OOB update
        json_html = render_to_string(
            "partials/clips_json_oob.html",
            {
                "clips_json": json.dumps(clips_json_data),
            },
        )

        # Render placeholder for form with OOB swap
        form_placeholder = render_to_string("partials/clip_form_placeholder.html")
        form_html = (
            f'<div hx-swap-oob="innerHTML:#detail-form">{form_placeholder}</div>'
        )

        response = HttpResponse(json_html + form_html)
        return response
    except Exception as e:
        logger.error(f"Error deleting clip {clip_id}: {e}")
        return HttpResponseServerError()


@require_GET
def subtitle_editor(request, content_id):
    try:
        content = get_object_or_404(Content, id=content_id)
        subtitle_files = Subtitle.objects.filter(resource=content.file.resource)
        subtitle_options = []
        for sub_file in subtitle_files:
            subtitle_options.append(
                {
                    "name": sub_file.name,
                    "id": sub_file.pk,
                }
            )
        file_key = request.user.get_filekey(content)
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
        "subtitle_editor.html",
        {
            "content": content,
            "subtitle_tracks": subtitle_options,
            "file_key": file_key,
            "events": player_json["annotations"],
            "subtitles": player_json["subtitleTracks"],
            "clips": player_json["clips"],
            "has_subtitles": has_subtitles,
        },
    )


def request_content(request):

    if request.method == "POST":
        form = ResourceContentIntakeRequestForm(request.POST)
        if form.is_valid():
            content_request = form.save(commit=False)
            content_request.owner = request.user
            content_request.save()
            return redirect("request_content")
    else:
        form = ResourceContentIntakeRequestForm()

    return render(
        request,
        "partials/resource_content_intake_request.html",
        {
            "form": form,
        },
    )


def add_collection_member(request, collection_id):
    allowed_privilege_levels = [0, 2]
    if request.user.privilege_level not in allowed_privilege_levels:
        return HttpResponse("Forbidden", status=403)

    collection = get_object_or_404(Collection, pk=collection_id)

    if request.method == "POST":
        # Get form data
        netid = request.POST.get("netid", "").strip()
        role = request.POST.get("role", "").strip()

        user = get_object_or_404(User, netid=netid)

        if role == "TA":
            collection_role = CollectionRole.TA
        else:
            collection_role = CollectionRole.AUDITOR

        CollectionUserAccess.objects.create(
            user=user,
            collection=collection,
            collection_role=collection_role,
        )

        return redirect("view_collection", pk=collection.id)

    return HttpResponseBadRequest()
