from functools import wraps
import json
import logging
import mimetypes
import os
import re

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseServerError
from django.http import JsonResponse
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .forms import CollectionForm
from .forms import CollectionSettingsForm
from .forms import ContentForm
from .forms import ImportantWordForm
from .forms import UpdateContentForm
from .models import BlankAnnotation
from .models import Collection
from .models import Content
from .models import FileKey
from .models import ImportantWord
from .models import MuteAnnotation
from .models import SkipAnnotation
from .models import User
from .models import UserCourses

logger = logging.getLogger(__name__)

TOY_VTT = """WEBVTT

00:00.000 --> 00:00.900
Hildy!

00:01.000 --> 00:01.400
How are you?

00:01.500 --> 00:02.900
Tell me, is the lord of the universe in?

00:03.000 --> 00:04.200
Yes, he's in - in a bad humor

00:04.300 --> 00:06.000
Somebody must've stolen the crown jewels"""

TOY_VTT2 = """WEBVTT

00:00.000 --> 00:00.900
Birds!

00:01.000 --> 00:01.400
Where are they?

00:01.500 --> 00:02.900
You don't know?

00:03.000 --> 00:04.200
Yes, but I want to know if you do.

00:04.300 --> 00:06.000
Oh, well I know too, so we don't have to say.

00:06.000 --> 00:06.900
Look outside!

00:07.000 --> 00:07.900
They're flying everywhere.

00:08.000 --> 00:08.900
Did you see the blue one?

00:09.000 --> 00:12.900
Yes, it landed on the fence.

00:10.000 --> 00:10.900
What about the red one?

00:11.000 --> 00:11.900
It was chasing the yellow.

00:12.000 --> 00:12.900
The flock is growing.

00:13.000 --> 00:13.900
They're singing loudly.

00:14.000 --> 00:14.900
Do you hear that melody?

00:15.000 --> 00:15.900
It's beautiful, isn't it?

00:16.000 --> 00:16.900
They must be happy.

00:17.000 --> 00:17.900
The sun is shining.

00:18.000 --> 00:18.900
Perfect day for birds.

00:19.000 --> 00:19.900
Let's watch them together.

00:20.000 --> 00:20.900
Maybe they'll come closer.
"""


def admin_or_superuser_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not getattr(request, "can_spoof", False):
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        return view_func(request, *args, **kwargs)

    return _wrapped


def prepare_collection_for_display(collection):
    published_contents = Content.objects.filter(collection=collection).filter(
        published=True
    )
    contents_count = published_contents.count()
    parsed_collection = {
        "name": collection.name,
        "items_display": f"{contents_count} items"
        if contents_count > 1 or contents_count == 0
        else f"{contents_count} item",
        "published_contents": published_contents,
    }
    return parsed_collection


def index(request):
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

    user_courses = UserCourses.objects.filter(user=request.user)
    collections_by_course = []
    for user_course in user_courses:
        course_name = user_course.course.__str__()
        course_collections = Collection.objects.filter(courses=user_course.course)
        collections_by_course.append(
            {
                "name": course_name,
                "collections": [
                    prepare_collection_for_display(collection)
                    for collection in course_collections
                ],
            }
        )

    context = {
        "user": request.user,
        "owned_collections": owned_collections,
        "assigned_collections": collections_by_course,
        "public_collections": [],
    }
    return render(request, "index.html", context)


def player(request, content_id):
    """Render the video player page."""
    content = get_object_or_404(Content, id=content_id)
    file_key = None
    if content.file:
        file_key = FileKey.objects.filter(file=content.file, user=request.user).first()

    # Prepare subtitle data in the format expected by AnnotationPlayer
    subtitles_data = [
        {"srclang": "en", "vtt": TOY_VTT, "label": "His Girl Friday"},
        {"srclang": "en", "vtt": TOY_VTT2, "label": "Birds"},
        # {"srclang": "en", "url": "http://example.com/subtitles.vtt", "label": "Birds"},
    ]

    clips_data = [
        {"start": 5, "end": 10, "label": "Sample Clip"},
        {"start": 11, "end": 14, "label": "Another Clip"},
        {"start": 15, "end": 20, "label": "Final Clip"},
    ]
    has_subtitles = bool(any(x.get("vtt") or x.get("url") for x in subtitles_data))

    context = {
        "content": content,
        "file_key": file_key.id if file_key else None,
        "allow_events": True,
        "events": json.dumps([]),
        "subtitles": json.dumps(subtitles_data),
        "clips": json.dumps(clips_data),
        "has_subtitles": has_subtitles,
    }

    return render(request, "player.html", context)


def stream_file(request, file_key):
    """Stream file content with support for HTTP Range requests (partial content)."""
    try:
        # Get the FileKey object
        file_key_obj = get_object_or_404(FileKey, id=file_key)
        file_obj = file_key_obj.file

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

    except FileKey.DoesNotExist:
        raise Http404("Invalid file key")
    except Exception as e:
        return HttpResponse(f"Error streaming file: {str(e)}", status=500)


def get_collection_types(user):
    collections = Collection.objects.filter(owner=user)

    archived = collections.filter(archived=True)
    published = collections.filter(archived=False, published=True)
    unpublished = collections.filter(archived=False, published=False)
    return {"archived": archived, "published": published, "unpublished": unpublished}


def manage_collections(request):
    collections = get_collection_types(request.user)

    return render(
        request,
        "manage_collections.html",
        {
            "published": collections["published"],
            "unpublished": collections["unpublished"],
            "archived": collections["archived"],
            "user": request.user,
            "form": CollectionForm(),
        },
    )


def create_collection(request):
    form = CollectionForm(request.POST, initial={"user": request.user})

    if form.is_valid():
        try:
            collection = form.save(commit=False)
            collection.owner = request.user
            collection.published = False
            collection.archived = False
            collection.public = False
            collection.save()

            collections = get_collection_types(request.user)

            response = render(
                request,
                "partials/collection_lists.html",
                {
                    "published": collections["published"],
                    "unpublished": collections["unpublished"],
                    "archived": collections["archived"],
                },
            )

        except Exception as e:
            logger.error(
                f"An error occured when the user: {collection.owner} attempted to create the collection: {collection.name} -> {e}"
            )

            response = HttpResponseServerError()

    else:
        response = HttpResponseBadRequest()

    return response


def view_collection(request, pk):
    user = request.user

    if request.method == "GET":
        collection_pk = pk
        collection = get_object_or_404(Collection, owner=user, pk=collection_pk)
        contents = Content.objects.filter(collection=collection)
        context = {
            "collection": collection,
            "contents": contents,
        }
        return render(request, "partials/view_collection.html", context)
    elif request.method == "PUT":
        collection_pk = pk
        collection = get_object_or_404(Collection, owner=user, pk=collection_pk)

        data = QueryDict(request.body).dict()
        content_pk = data.get("content_id")

        if not content_pk:
            logger.error("No content_id provided in PUT request")
            return HttpResponse("Content ID is required", 400)

        updated_content = get_object_or_404(Content, pk=content_pk)
        form = UpdateContentForm(data, instance=updated_content)

        if form.is_valid():
            try:
                form.save()
                return render(
                    request,
                    "partials/content_display.html",
                    {"content": updated_content},
                )
            except Exception as e:
                logger.warning(
                    f"An error occured when the user: {collection.owner} attempted to update the content: {updated_content.title} -> {e}"
                )
                response = render(
                    request,
                    "partials/edit_content.html",
                    {"content": updated_content, "form": form},
                )
                return response
        else:
            response = render(
                request,
                "partials/edit_content.html",
                {"content": updated_content, "form": form},
            )
            return response


def display_collection_settings(request, collection_id):
    collection = get_object_or_404(Collection, pk=collection_id)
    form = CollectionSettingsForm(instance=collection)
    context = {"collection": collection, "form": form}
    return render(request, "partials/collection_settings.html", context)


@require_POST
def update_collection_settings(request):
    form = CollectionSettingsForm(request.POST)
    if form.is_valid():
        collection = get_object_or_404(Collection, pk=form.cleaned_data["id"])
        collection.name = form.cleaned_data["name"]
        collection.published = form.cleaned_data["published"]
        collection.archived = form.cleaned_data["archived"]
        try:
            collection.save()
            collection_types = get_collection_types(request.user)
            context = {
                "collection": collection,
                "published": collection_types["published"],
                "unpublished": collection_types["unpublished"],
                "archived": collection_types["archived"],
            }
            return render(request, "partials/finish_collection_settings.html", context)
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


def display_collection_contents(request, collection_id):
    collection = get_object_or_404(Collection, pk=collection_id)
    contents = get_collection_contents(collection)
    context = {
        "collection": collection,
        "published_contents": contents["published"],
        "unpublished_contents": contents["unpublished"],
    }
    return render(request, "partials/collection_contents_display.html", context)


@require_http_methods(["DELETE"])
def delete_collection(request, collection_id):
    collection = get_object_or_404(Collection, pk=collection_id)
    try:
        collection.delete()
        collections = get_collection_types(request.user)
        context = {
            "published": collections["published"],
            "unpublished": collections["unpublished"],
            "archived": collections["archived"],
        }
        return render(request, "partials/finish_collection_deletion.html", context)
    except Exception as e:
        logger.error(
            f"An error occured while deleting the collection with id: {collection_id}. Exception: {e}"
        )
        return HttpResponseServerError()
    return HttpResponseBadRequest()


@require_GET
def display_create_content(request, collection_id):
    form = ContentForm()
    collection = get_object_or_404(Collection, pk=collection_id)
    return render(
        request,
        "partials/create_content.html",
        {"form": form, "collection": collection},
    )


@require_POST
def create_content(request):
    collection = get_object_or_404(Collection, pk=request.POST["collection_id"])
    form = ContentForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            Content.objects.create(
                collection=collection,
                title=data["title"],
                description=data["description"],
                allow_definitions=data["allow_definitions"],
                allow_notes=data["allow_notes"],
                allow_captions=data["allow_captions"],
            )
        except Exception as e:
            logger.error(
                f"An error occured while creating a new Content. Exception: {e}"
            )
            return HttpResponseServerError()

        try:
            contents = get_collection_contents(collection)
        except Exception as e:
            logger.error(
                f"An error occured while trying to gather collection contents after content creation. Exception: {e}"
            )
            return HttpResponseServerError()

        context = {
            "collection": collection,
            "published_contents": contents["published"],
            "unpublished_contents": contents["unpublished"],
        }
        return render(request, "partials/collection_contents_display.html", context)
    else:
        return HttpResponseBadRequest()


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


@require_POST
def update_content(request):
    form = UpdateContentForm(request.POST)
    if form.is_valid():
        content = get_object_or_404(Content, pk=form.cleaned_data["id"])
        content.title = form.cleaned_data["title"]
        content.description = form.cleaned_data["description"]
        if "allow_definitions" in form.cleaned_data:
            content.allow_definitions = form.cleaned_data["allow_definitions"]
        if "allow_notes" in form.cleaned_data:
            content.allow_notes = form.cleaned_data["allow_notes"]
        if "allow_captions" in form.cleaned_data:
            content.allow_captions = form.cleaned_data["allow_captions"]
        if "published" in form.cleaned_data:
            content.published = form.cleaned_data["published"]
        try:
            content.save()
            contents = get_collection_contents(content.collection)
            context = {
                "collection": content.collection,
                "published_contents": contents["published"],
                "unpublished_contents": contents["unpublished"],
            }
            return render(request, "partials/collection_contents_display.html", context)
        except Exception as e:
            logger.error(f"An error occured while updating content. Exception: {e}")
            return HttpResponseServerError()
    else:
        return HttpResponseBadRequest()


def display_content_settings(request, content_id):
    content = get_object_or_404(Content, pk=content_id)
    form = UpdateContentForm(instance=content)
    word_form = ImportantWordForm()
    words = ImportantWord.objects.filter(content=content)
    context = {"content": content, "form": form, "word_form": word_form, "words": words}
    return render(request, "partials/content_settings.html", context)


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

    # Get the File object
    content_obj = get_object_or_404(Content, id=content_id)

    # Get POST data
    name = request.POST.get("name", "")
    owner = request.POST.get("owner", "")  # Change to request.user when auth is set up
    start_time = float(request.POST.get("start_time", 0))
    end_time = float(request.POST.get("end_time", 0))

    # Create the annotation
    annotation = annotation_class.objects.create(
        content=content_obj,
        owner=owner,  # change to request.user when auth is set up
        name=name,
        start_time=start_time,
        end_time=end_time,
    )

    # Return JSON response
    return JsonResponse(
        {
            "id": annotation.id,
            "name": annotation.name,
            "type": annotation.annotation_type,
            "owner": annotation.owner.netid,
            "start_time": annotation.start_time,
            "end_time": annotation.end_time,
        }
    )


@login_not_required
def invalid_login(request):
    return render(request, "invalid_login.html", {})


@admin_or_superuser_required
@login_not_required
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
@login_not_required
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
            | Q(netid__icontains=query)
        )
        & ~Q(id=request.user.id)
    ).order_by("last_name")[:25]
    html = render_to_string(
        "partials/spoof_user_options_for_select.html", {"users": users}
    )
    return HttpResponse(html)
