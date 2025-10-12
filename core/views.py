from functools import wraps
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
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.template.loader import render_to_string

from core.forms import CollectionForm

from .models import BlankAnnotation
from .models import Collection
from .models import Content
from .models import FileKey
from .models import MuteAnnotation
from .models import SkipAnnotation
from .models import User

logger = logging.getLogger(__name__)


def admin_or_superuser_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not getattr(request, "can_spoof", False):
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        return view_func(request, *args, **kwargs)

    return _wrapped


def index(request):
    collections = Collection.objects.filter(owner=request.user)
    all_contents = Content.objects.filter(collection__in=collections)
    filtered_contents = {collection: [] for collection in collections}

    for content in all_contents:
        filtered_contents[content.collection].append(content)

    context = {
        "user": request.user,
        "collections": collections,
        "contents": filtered_contents,
        "public_collections": [],
    }
    return render(request, "index.html", context)


def player(request, content_id):
    """Render the video player page."""
    content = get_object_or_404(Content, id=content_id)
    file_key = None
    if content.file:
        file_key = FileKey.objects.filter(file=content.file, user=request.user).first()

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
