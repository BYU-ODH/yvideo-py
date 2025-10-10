import logging
import mimetypes
import os
import re

from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseServerError
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .forms import CollectionForm
from .forms import CollectionSettingsForm
from .forms import ImportantWordForm
from .forms import UpdateContentForm
from .models import Collection
from .models import Content
from .models import FileKey
from .models import ImportantWord
from .models import User

logger = logging.getLogger(__name__)


def index(request):
    # user = request.user
    user = User.objects.all().first()
    collections = Collection.objects.filter(owner=user)
    all_contents = Content.objects.filter(collection__in=collections)
    filtered_contents = {collection: [] for collection in collections}

    for content in all_contents:
        filtered_contents[content.collection].append(content)

    context = {
        "user": user,  # TODO: Replace with actual data
        "collections": collections,
        "contents": filtered_contents,
        "public_collections": [],
    }
    return render(request, "index.html", context)


def login(request):
    """
    This is a stub function until SAML is working properly. Until then,
    it isn't clear what steps should be taken to complete this method.
    When the SAML integration is completed, this method will need to
    get the byu_id from the SAML response and create a user if one does
    not already exist.
    """
    pass


def player(request, content_id):
    """Render the video player page."""
    content = get_object_or_404(Content, id=content_id)
    user = User.objects.first()  # TODO: Delete
    # user = request.user  # TODO: Uncomment
    if content.file:
        file_key = FileKey.objects.filter(file=content.file, user=user).first()

    context = {
        "content": content,
        "file_key": file_key.id if file_key else None,
        "allow_events": True,
        "events": [],
        "subtitles": [],
        "clips": [],
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
    # collections = Collection.objects.filter(owner=request.user)
    collections = get_collection_types(User.objects.all().first())

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
                "partials/finish_adding_collection.html",
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
    # user = request.user
    user = User.objects.all().first()

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


def display_content_settings(request, content_id):
    content = get_object_or_404(Content, pk=content_id)
    form = UpdateContentForm(instance=content)
    word_form = ImportantWordForm()
    words = ImportantWord.objects.filter(content=content)
    context = {"content": content, "form": form, "word_form": word_form, "words": words}
    return render(request, "partials/content_settings.html", context)


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
