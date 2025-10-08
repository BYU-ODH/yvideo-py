import json
import logging
import mimetypes
import os
import re

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render

from core.forms import CollectionForm

from .models import Collection
from .models import Content
from .models import FileKey
from .models import User

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


@login_required
def index(request):
    user = request.user
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


# @login_required  # TODO: Uncomment
def player(request, content_id):
    """Render the video player page."""
    content = get_object_or_404(Content, id=content_id)
    user = User.objects.first()  # TODO: Delete
    # user = request.user  # TODO: Uncomment
    if content.file:
        file_key = FileKey.objects.filter(file=content.file, user=user).first()

    # Prepare subtitle data in the format expected by AnnotationPlayer
    subtitles_data = [
        {"srclang": "en", "vtt": TOY_VTT, "label": "His Girl Friday"},
        {"srclang": "en", "vtt": TOY_VTT2, "label": "Birds"},
    ]

    has_subtitles = bool(any(x.get("vtt") or x.get("url") for x in subtitles_data))

    context = {
        "content": content,
        "file_key": file_key.id if file_key else None,
        "allow_events": True,
        "events": json.dumps([]),
        "subtitles": json.dumps(subtitles_data),
        "clips": json.dumps([]),
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


@login_required
def manage_collections(request):
    collections = Collection.objects.filter(owner=request.user)

    archived = collections.filter(archived=True)
    published = collections.filter(archived=False, published=True)
    unpublished = collections.filter(archived=False, published=False)

    return render(
        request,
        "manage_collections.html",
        {
            "published": published,
            "unpublished": unpublished,
            "archived": archived,
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

            response = render(
                request, "partials/load_collection.html", {"collection": collection}
            )
            response["HX-Trigger"] = "success"
        except Exception as e:
            logger.warning(
                f"An error occured when the user: {collection.owner} attempted to create the collection: {collection.name} -> {e}"
            )

            response = render(
                request, "partials/add_collection_modal.html", {"form": form}
            )
            response["HX-Retarget"] = "#collection_modal"
            response["HX-Reswap"] = "outerHTML"
            response["HX-Trigger-After-Settle"] = "fail"
    else:
        response = render(request, "partials/add_collection_modal.html", {"form": form})
        response["HX-Retarget"] = "#collection_modal"
        response["HX-Reswap"] = "outerHTML"
        response["HX-Trigger-After-Settle"] = "fail"

    return response


def invalid_login(request):
    return render(request, "invalid_login.html", {})
