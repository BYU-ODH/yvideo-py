import json
import logging

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseServerError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .models import AnnotationSet
from .models import BlankAnnotation
from .models import BlurAnnotation
from .models import CommentAnnotation
from .models import Content
from .models import MuteAnnotation
from .models import PauseAnnotation
from .models import SkipAnnotation
from .models import User

logger = logging.getLogger(__name__)

# Map annotation type strings to model classes
ANNOTATION_MODELS = {
    "skip": SkipAnnotation,
    "mute": MuteAnnotation,
    "blank": BlankAnnotation,
    "pause": PauseAnnotation,
    "censor": BlurAnnotation,
    "comment": CommentAnnotation,
}


def build_annotation_layers(content, annotation_set, can_edit):
    layers = []
    layer_buttons = {}
    for type_name, model_class in ANNOTATION_MODELS.items():
        layer_items = []
        if annotation_set:
            annotations = model_class.objects.filter(
                annotation_set=annotation_set, active=True
            ).order_by("start_time")
            for annotation in annotations:
                start_time = annotation.start_time
                end_time = getattr(annotation, "end_time", start_time)
                layer_items.append(
                    {
                        "instance": annotation,
                        "content": content,
                        "item_type": type_name,
                        "update_url": "update_annotation",
                        "load_form_url": "load_annotation_form",
                        "position": {
                            "start": start_time,
                            "end": end_time,
                        },
                    }
                )

        # Add to list (for timeline_layers.html)
        layers.append(
            {
                "type": type_name,
                "label": type_name.title(),
                "can_edit": can_edit,
                "items": layer_items,
                "add_button": {
                    "post_url": reverse(
                        "create_annotation", args=[type_name, content.pk]
                    ),
                    "vals": "js:...getNewItemStartEndTimes()",
                    "swap": "none",
                    "title": f"Add new {type_name} annotation",
                }
                if can_edit
                else None,
            }
        )

        layer_buttons[type_name] = {
            "display_name": type_name.title(),
            "can_add": can_edit,
            "add_form_url": "create_annotation",
        }
    return {"layers": layers, "layer_buttons": layer_buttons}


def build_timeline_layers_html(request, content, annotation_set, can_edit):
    layers_build_result = build_annotation_layers(content, annotation_set, can_edit)
    layers = layers_build_result["layers"]
    timeline_layers_html = render_to_string(
        "core/partials/timeline_layers.html",
        {
            "layers": layers,
        },
        request=request,
    )
    return timeline_layers_html


def return_annotation_if_authorized_and_exists(
    annotation_set, user, annotation_id, annotation_type
):
    if not annotation_set:
        return {
            "success": False,
            "result": HttpResponse("No active annotation set", status=400),
        }

    # Check edit permissions
    if not annotation_set.can_edit(user):
        return {
            "success": False,
            "result": HttpResponse("Cannot edit this AnnotationSet", status=403),
        }

    if not annotation_id or not annotation_type:
        return {
            "success": False,
            "result": HttpResponse(
                "No annotation_id or annotation_type provided", status=400
            ),
        }

    # Use annotation_type to get the correct model
    model_class = ANNOTATION_MODELS.get(annotation_type.lower())
    if not model_class:
        return {
            "success": False,
            "result": HttpResponse(
                f"Unknown annotation type: {annotation_type}", status=400
            ),
        }

    try:
        annotation = model_class.objects.get(id=annotation_id, active=True)
    except model_class.DoesNotExist:
        return {
            "success": False,
            "result": HttpResponse("Annotation not found or inactive", status=404),
        }

    return {"success": True, "result": annotation}


@require_GET
@login_required
def video_editor(request, content_id):
    """Main video editor page."""
    content = get_object_or_404(Content, id=content_id)

    # Check if user can view this content
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    # Get available annotation sets
    available_sets = content.get_available_annotation_sets()

    # Determine if user can edit the active annotation set
    annotation_set = content.annotation_set
    can_edit = annotation_set.can_edit(request.user) if annotation_set else True

    can_edit_annotation_set = annotation_set is not None and (
        annotation_set.owner == request.user or request.user.is_admin
    )

    # Get file key for video streaming
    file_key = request.user.get_resource_filekey(content)

    # Prepare layer data for timeline
    layer_results = build_annotation_layers(content, annotation_set, can_edit)

    context = {
        "content": content,
        "content_id": content_id,
        "file_key": file_key.id if file_key else None,
        "allow_events": True,
        "available_annotation_sets": available_sets,
        "annotation_set": annotation_set,
        "can_edit": can_edit,
        "can_edit_annotation_set": can_edit_annotation_set,
        "layers": layer_results["layers"],  # For timeline_layers.html content
        "layer_buttons": layer_results[
            "layer_buttons"
        ],  # For timeline_base.html label column
    }

    return render(request, "core/video_editor.html", context)


@login_required
def load_annotation_set_form(request, content_id):
    """Load the annotation set selection form."""
    content = get_object_or_404(Content, id=content_id)

    # Check permissions
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    annotation_set = content.annotation_set

    can_edit = annotation_set.can_edit(request.user) if annotation_set else True

    can_edit_annotation_set = annotation_set is not None and (
        annotation_set.owner == request.user or request.user.is_admin
    )

    form_html = render_to_string(
        "core/partials/annotation_set_form.html",
        {
            "content": content,
            "annotation_set": annotation_set,
            "can_edit": can_edit,
            "can_edit_annotation_set": can_edit_annotation_set,
            "available_annotation_sets": content.get_available_annotation_sets(),
        },
        request=request,
    )

    return HttpResponse(form_html)


@require_POST
@login_required
def select_annotation_set(request):
    """Switch the active AnnotationSet for a content."""
    body = json.loads(request.body.decode("utf-8"))
    content_id = body["content_id"]
    content = get_object_or_404(Content, pk=content_id)

    # Check permissions
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    annotation_set_id = body["annotation_set_id"]

    if not annotation_set_id:
        logger.error(f"Annotation set id was not provided: {annotation_set_id}")
        return HttpResponseBadRequest()

    annotation_set = get_object_or_404(
        AnnotationSet, id=annotation_set_id, resource=content.resource_file.resource
    )
    if not annotation_set.can_be_viewed_by(request.user):
        return HttpResponse("Unauthorized", status=403)

    content.annotation_set = annotation_set
    content.save()

    can_edit = annotation_set.can_edit(request.user) if annotation_set else True

    # Prepare layers for timeline rendering (matching clip editor structure)
    layer_results = build_annotation_layers(content, annotation_set, can_edit)
    layers = layer_results["layers"]

    # Render timeline using shared partial
    timeline_layers_html = render_to_string(
        "core/partials/timeline_layers.html",
        {"layers": layers, "content_id": content_id},
        request=request,
    )

    # Get JSON from model method
    video_html = render_to_string(
        "partials/player-wrapper.html",
        {
            "content_id": content_id,
            "resource_file_key_id": request.user.get_resource_filekey(content).pk,
        },
        request=request,
    )

    return JsonResponse(
        {"video_section": video_html, "timeline_layers": timeline_layers_html}
    )


@require_POST
@login_required
def undo_annotation(request, content_id):
    """Undo the last annotation edit for a specific annotation."""
    content = get_object_or_404(Content, id=content_id)
    annotation_set = content.annotation_set

    # Get both annotation ID and type from POST data
    annotation_id = request.POST.get("annotation_id")
    annotation_type = request.POST.get("annotation_type")

    annotation_response = return_annotation_if_authorized_and_exists(
        annotation_set, request.user, annotation_id, annotation_type
    )
    if not annotation_response["success"]:
        return annotation_response["result"]
    annotation = annotation_response["result"]

    # Perform undo on this specific annotation
    prev_version = annotation.undo()
    if not prev_version:
        return HttpResponse("Nothing to undo for this annotation", status=400)

    # Prepare layers for timeline rendering
    timeline_layers_html = build_timeline_layers_html(
        request, content, annotation_set, True
    )
    if timeline_layers_html:
        return HttpResponse(timeline_layers_html)
    else:
        return HttpResponseServerError()


@require_POST
@login_required
def redo_annotation(request, content_id):
    """Redo the last undone annotation edit for a specific annotation."""
    content = get_object_or_404(Content, id=content_id)
    annotation_set = content.annotation_set

    # Get both annotation ID and type from POST data
    annotation_id = request.POST.get("annotation_id")
    annotation_type = request.POST.get("annotation_type")

    annotation_response = return_annotation_if_authorized_and_exists(
        annotation_set, request.user, annotation_id, annotation_type
    )
    if not annotation_response["success"]:
        return annotation_response["result"]
    annotation = annotation_response["result"]

    # Perform redo on this specific annotation
    next_version = annotation.redo()
    if not next_version:
        return HttpResponse("Nothing to redo for this annotation", status=400)

    # Prepare layers for timeline rendering
    timeline_layers_html = build_timeline_layers_html(
        request, content, annotation_set, True
    )
    if timeline_layers_html:
        return HttpResponse(timeline_layers_html)
    else:
        return HttpResponseServerError()


@require_POST
@login_required
def add_editor_to_annotation_set(request, annotation_set_id):
    """Add a user as an editor to an AnnotationSet."""
    annotation_set = get_object_or_404(AnnotationSet, id=annotation_set_id)

    # Only owner can add editors
    if request.user != annotation_set.owner:
        return HttpResponse("Unauthorized", status=403)

    username = request.POST.get("username")
    user = get_object_or_404(User, username=username)

    annotation_set.editors.add(user)

    # Re-render annotation set form with updated editors
    content_id = request.POST.get("content_id")
    content = get_object_or_404(Content, id=content_id)

    form_html = render_to_string(
        "core/partials/annotation_set_form.html",
        {
            "content": content,
            "annotation_set": annotation_set,
            "can_edit": True,
            "available_annotation_sets": content.get_available_annotation_sets(),
        },
        request=request,
    )

    return HttpResponse(form_html)


@require_http_methods(["DELETE"])
@login_required
def remove_editor_from_annotation_set(request, annotation_set_id, user_id):
    """Remove a user as an editor from an AnnotationSet."""
    annotation_set = get_object_or_404(AnnotationSet, id=annotation_set_id)

    # Only owner can remove editors
    if request.user != annotation_set.owner:
        return HttpResponse("Unauthorized", status=403)

    user = get_object_or_404(User, id=user_id)
    annotation_set.editors.remove(user)

    # Re-render annotation set form
    content_id = request.GET.get("content_id")
    content = get_object_or_404(Content, id=content_id)

    form_html = render_to_string(
        "core/partials/annotation_set_form.html",
        {
            "content": content,
            "annotation_set": annotation_set,
            "can_edit": True,
            "available_annotation_sets": content.get_available_annotation_sets(),
        },
        request=request,
    )

    return HttpResponse(form_html)


@require_POST
@login_required
@transaction.atomic
def create_annotation(request, annotation_type, content_id):
    """Create annotation in the active AnnotationSet."""

    content = get_object_or_404(Content, id=content_id)
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    annotation_set = content.annotation_set
    if not annotation_set:
        return HttpResponse("No active AnnotationSet", status=404)

    if not annotation_set.can_edit(request.user):
        return HttpResponse("Cannot edit this AnnotationSet", status=403)

    model_class = ANNOTATION_MODELS.get(annotation_type.lower())
    if not model_class:
        return HttpResponse(f"Unknown annotation type: {annotation_type}", status=400)

    start_time = float(request.POST.get("start_time", 0))
    end_time = (
        float(request.POST.get("end_time", 0))
        if annotation_type != "pause"
        else start_time
    )

    data = {
        "annotation_set": annotation_set,
        "owner": request.user,
        "name": f"{annotation_type.title()} {model_class.objects.filter(annotation_set=annotation_set).count() + 1}",
        "start_time": start_time,
        "active": True,
        "prev": None,
        "next": None,
    }

    if annotation_type != "pause":
        data["end_time"] = end_time

    if annotation_type == "censor":
        data["positions"] = {}
    elif annotation_type == "comment":
        data.update({"text": "", "x": 50.0, "y": 50.0})
    elif annotation_type == "pause":
        data["message"] = ""
    elif annotation_type == "blank":
        data["type"] = "k"

    annotation = model_class.objects.create(**data)

    # Calculate position
    position = {
        "start": start_time,
        "end": end_time,
    }

    # Render item using shared partial
    item_html = render_to_string(
        "partials/item.html",
        {
            "instance": annotation,
            "content": content,
            "item_type": annotation_type,
            "update_url": "update_annotation",
            "load_form_url": "load_annotation_form",
            "position": position,
        },
        request=request,
    )

    return HttpResponse(item_html)


def validate_annotation_update_request(user, content, annotation_type, annotation_id):
    if not user.can_view_content(content):
        return {"success": False, "result": HttpResponse("Unauthorized", status=403)}

    # Use the annotation_type parameter to get the correct model
    model_class = ANNOTATION_MODELS.get(annotation_type.lower())
    if not model_class:
        return {
            "success": False,
            "result": HttpResponse(
                f"Unknown annotation type: {annotation_type}", status=400
            ),
        }

    try:
        annotation = model_class.objects.get(id=annotation_id, active=True)
    except model_class.DoesNotExist:
        return {
            "success": False,
            "result": HttpResponse("Annotation not found or inactive", status=404),
        }

    if not annotation.annotation_set.can_edit(user):
        return {
            "success": False,
            "result": HttpResponse("Cannot edit this AnnotationSet", status=403),
        }

    return {"success": True, "result": annotation}


def generate_annotation_updated_html(
    request, content, annotation, annotation_type, position
):
    item_html = render_to_string(
        "partials/item.html",
        {
            "instance": annotation,
            "content": content,
            "item_type": annotation_type,
            "update_url": "update_annotation",
            "load_form_url": "load_annotation_form",
            "position": position,
        },
        request=request,
    )

    form_html = render_to_string(
        "core/partials/annotation_form.html",
        {
            "instance": annotation,
            "content": content,
            "can_edit": True,
            "item_type": annotation_type,
            "item_type_label": annotation_type.title(),
            "update_url": "update_annotation",
            "delete_url": "delete_annotation",
            "start_seconds": position["start"],
            "end_seconds": position["end"],
        },
        request=request,
    )

    return {"item_html": item_html, "form_html": form_html}


def update_annotation_from_form(request, annotation_type, annotation_id):
    parsed_body = json.loads(request.body)
    content_id = parsed_body["content_id"]
    content = get_object_or_404(Content, id=content_id)
    validation_result = validate_annotation_update_request(
        request.user, content, annotation_type, annotation_id
    )
    if not validation_result["success"]:
        return validation_result["result"]
    annotation = validation_result["result"]

    update_fields = {}
    update_fields["name"] = parsed_body["name"]
    update_fields["start_time"] = parsed_body["start_time"]
    update_fields["description"] = parsed_body["description"]

    if annotation_type != "pause":
        update_fields["end_time"] = parsed_body["end_time"]

    if annotation_type == "censor":
        positions_json = parsed_body["positions"]
        if positions_json:
            positions = json.loads(positions_json)
            is_valid, error = BlurAnnotation.validate_positions(positions)
            if not is_valid:
                return HttpResponse(error, status=400)
            update_fields["positions"] = positions
    elif annotation_type == "comment":
        update_fields["text"] = parsed_body["text"]
        x = parsed_body["x"]
        y = parsed_body["y"]
        if x is not None:
            update_fields["x"] = float(x)
        if y is not None:
            update_fields["y"] = float(y)
    elif annotation_type == "pause":
        update_fields["message"] = parsed_body["message"]
    elif annotation_type == "blank":
        update_fields["type"] = parsed_body["blank_type"]

    for field, value in update_fields.items():
        setattr(annotation, field, value)

    annotation.save()

    # Update the data-label attribute on the element
    annotation.refresh_from_db()

    # Calculate new position
    start_time = annotation.start_time
    end_time = getattr(annotation, "end_time", start_time)

    position = {
        "start": start_time,
        "end": end_time,
    }

    new_html = generate_annotation_updated_html(
        request, content, annotation, annotation_type, position
    )
    item_html = new_html["item_html"]
    form_html = new_html["form_html"]

    return JsonResponse({"item_html": item_html, "form_html": form_html})


def update_annotation_from_item(request, annotation_type, annotation_id):
    """Update annotation by creating a new version in the linked list."""
    parsed_post = json.loads(request.body)
    content_id = parsed_post["content_id"]
    content = get_object_or_404(Content, id=content_id)

    validation_result = validate_annotation_update_request(
        request.user, content, annotation_type, annotation_id
    )
    if not validation_result["success"]:
        return validation_result["result"]
    annotation = validation_result["result"]

    annotation.start_time = parsed_post["start_time"]
    if annotation_type != "pause":
        annotation.end_time = parsed_post["end_time"]

    annotation.save()

    # Update the data-label attribute on the element
    annotation.refresh_from_db()

    position = {
        "start": annotation.start_time,
        "end": annotation.end_time,
    }

    # Render updated item
    updated_html = generate_annotation_updated_html(
        request, content, annotation, annotation_type, position
    )
    item_html = updated_html["item_html"]
    form_html = updated_html["form_html"]

    return JsonResponse({"item_html": item_html, "form_html": form_html})


@require_POST
@login_required
@transaction.atomic
def update_annotation(request, annotation_type, annotation_id, is_from_item):
    try:
        if is_from_item:
            return update_annotation_from_item(request, annotation_type, annotation_id)
        else:
            return update_annotation_from_form(request, annotation_type, annotation_id)
    except Exception as e:
        logger.error(f"Failed to update annotation. Exception: {e}")
        return HttpResponseServerError()


@require_http_methods(["DELETE"])
@login_required
@transaction.atomic
def delete_annotation(request, annotation_type, annotation_id):
    """Delete annotation by marking it inactive."""
    # Use the annotation_type parameter to get the correct model
    model_class = ANNOTATION_MODELS.get(annotation_type.lower())
    if not model_class:
        return HttpResponse(f"Unknown annotation type: {annotation_type}", status=400)

    try:
        annotation = model_class.objects.get(id=annotation_id, active=True)
    except model_class.DoesNotExist:
        return HttpResponse("Annotation not found or inactive", status=404)

    # Check edit permissions
    if not annotation.annotation_set.can_edit(request.user):
        return HttpResponse("Cannot edit this AnnotationSet", status=403)

    # Use delete_with_history() to preserve undo capability
    try:
        annotation.delete_with_history()
    except Exception as e:
        logger.error(f"Exception occurred while deleting annotation: {e}")
        return HttpResponseServerError()

    return HttpResponse(status=200)


@require_GET
@login_required
def load_annotation_form(request, annotation_type, annotation_id):
    """Load form for editing an annotation."""
    # Use the annotation_type parameter to get the correct model
    model_class = ANNOTATION_MODELS.get(annotation_type.lower())
    if not model_class:
        return HttpResponse(f"Unknown annotation type: {annotation_type}", status=400)

    try:
        annotation = model_class.objects.get(id=annotation_id, active=True)
    except model_class.DoesNotExist:
        return HttpResponse("Annotation not found or inactive", status=404)

    content_id = request.GET.get("content_id")
    content = get_object_or_404(Content, id=content_id)

    can_edit = annotation.annotation_set.can_edit(request.user)

    start_seconds = annotation.start_time
    end_seconds = getattr(annotation, "end_time", start_seconds)

    form_html = render_to_string(
        "partials/annotation_form.html",
        {
            "instance": annotation,
            "content": content,
            "can_edit": can_edit,
            "item_type": annotation_type,
            "item_type_label": annotation_type.title(),
            "update_url": "update_annotation",
            "delete_url": "delete_annotation",
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
        },
        request=request,
    )

    return HttpResponse(form_html)


@require_GET
def create_annotation_form(request, content_id, annotation_type):
    """Load blank form for creating a new annotation."""
    # This view is no longer needed since we create annotations directly
    # But keeping it for backward compatibility if any links still reference it
    content = get_object_or_404(Content, id=content_id)

    # Check permissions
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    annotation_set = content.annotation_set
    if not annotation_set or not annotation_set.can_edit(request.user):
        return HttpResponse("Cannot edit this AnnotationSet", status=403)

    # Return empty response or redirect to main page
    return HttpResponse("")


@require_GET
@login_required
def load_annotation_set_settings(request, annotation_set_id):
    annotation_set = get_object_or_404(AnnotationSet, id=annotation_set_id)
    content_id = request.GET.get("content_id")
    content = get_object_or_404(Content, id=content_id)
    if not (request.user == annotation_set.owner or request.user.is_admin):
        return HttpResponse("Unauthorized", status=403)
    return render(
        request,
        "partials/annotation_set_settings_compact.html",
        {
            "annotation_set": annotation_set,
            "content": content,
        },
    )


@require_POST
@login_required
def update_annotation_set_name(request, annotation_set_id):
    annotation_set = get_object_or_404(AnnotationSet, id=annotation_set_id)
    if not (request.user == annotation_set.owner or request.user.is_admin):
        return HttpResponse("Unauthorized", status=403)
    name = request.POST.get("name", "").strip()
    if name:
        annotation_set.name = name
        annotation_set.save()
    content_id = request.POST.get("content_id")
    content = get_object_or_404(Content, id=content_id)
    return render(
        request,
        "core/partials/annotation_set_settings_compact.html",
        {
            "annotation_set": annotation_set,
            "content": content,
        },
    )
