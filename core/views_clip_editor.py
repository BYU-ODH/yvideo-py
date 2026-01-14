import json
import logging

from django.http import HttpResponse
from django.http import HttpResponseServerError
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .forms import ClipForm
from .models import Clip
from .models import Content
from .utils import TOY_VTT
from .utils import TOY_VTT2
from .utils import hms2seconds
from .utils import seconds2hms

logger = logging.getLogger(__name__)


# TODO add permission check
def clip_editor(request, content_id):
    """Render the clip editor page."""
    content = get_object_or_404(Content, id=content_id)
    file_key = request.user.get_resource_filekey(content)

    duration = content.duration
    layer_items = []

    for item in content.clips.all():
        start_time = hms2seconds(item.start_time)
        end_time = hms2seconds(item.end_time)
        start_percent = (start_time / duration * 100) if duration > 0 else 0
        width_percent = (
            ((end_time - start_time) / duration * 100) if duration > 0 else 0
        )

        layer_items.append(
            {
                "template": "partials/item.html",
                "instance": item,
                "content": content,
                "item_type": "clip",
                "update_url": "update_clip",
                "load_form_url": "load_clip_form",
                "position": {
                    "left": f"{start_percent:.2f}%",
                    "width": f"{width_percent:.2f}%",
                    "start": start_time,
                    "end": end_time,
                },
            }
        )

    # Get JSON from model method
    player_json = json.dumps(content.get_player_json(), indent=2)

    # Create layers as a list (for iteration in template)
    layers_list = [
        {
            "type": "clip",
            "label": "Clips",
            "can_edit": True,
            "items": layer_items,
            "add_button": {
                "post_url": reverse("create_clip", args=["clip", content_id]),
                "vals": "js:...getNewItemStartEndTimes()",
                "swap": "none",
                "title": "Add new clip at current time",
            },
        }
    ]

    # Create layers as dict (for the label column in timeline_base.html)
    layers_dict = {
        "clip": {
            "display_name": "Clips",
            "can_add": True,
            "add_form_url": "create_clip",
        }
    }

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
        "layers": layers_dict,  # For timeline_base.html label column
        "layers_list": layers_list,  # For timeline_layers.html content
    }

    return render(request, "clip_editor.html", context)


@require_GET
def load_clip_form(request, item_type, clip_id):
    """Load clip editing form via HTMX."""
    if item_type != "clip":
        return HttpResponse("Invalid item type", status=400)
    instance = get_object_or_404(Clip, id=clip_id)

    # Get content from query parameter (required for context)
    content_id = request.GET.get("content_id")
    if not content_id:
        return HttpResponse("Missing content_id parameter", status=400)

    content = get_object_or_404(Content, id=content_id)

    # Check permissions on the content
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    # Check if user can edit this item
    can_edit = instance.can_edit(request.user)

    form = ClipForm(instance=instance)

    context = {
        "instance": instance,
        "content": content,
        "can_edit": can_edit,
        "form": form,
        "item_type": "clip",
        "item_type_label": "Clip",
        "update_url": "update_clip",
        "delete_url": "delete_clip",
        "start_seconds": hms2seconds(instance.start_time),
        "end_seconds": hms2seconds(instance.end_time),
    }

    return render(request, "partials/item_form.html", context)


@require_POST
def update_clip(request, item_type, clip_id):
    """Update clip and return updated HTML with JSON OOB."""
    if item_type != "clip":
        return HttpResponse("Invalid item type", status=400)

    instance = get_object_or_404(Clip, id=clip_id)

    # Get content from POST data (required for context)
    content_id = request.POST.get("content_id")
    if not content_id:
        return HttpResponse("Missing content_id", status=400)

    content = get_object_or_404(Content, id=content_id)

    # Check permissions on the content
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    # If user doesn't own this item, clone it
    if not instance.can_edit(request.user):
        original_instance = instance
        instance = instance.clone_for_user(request.user)

        # Add the new item to the content (replacing the original)
        content.clips.remove(original_instance)
        content.clips.add(instance)
        content.save()

    # Check if this is a delta-based update (from drag/resize)
    delta_left = request.POST.get("delta_left")
    delta_width = request.POST.get("delta_width")

    if delta_left is not None or delta_width is not None:
        # Delta-based update from drag/resize
        duration = content.duration

        # Get current position percentages
        start_time = hms2seconds(instance.start_time)
        end_time = hms2seconds(instance.end_time)
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
                "instance": instance,
                "content": content,
                "item_type": "clip",
                "update_url": "update_clip",
                "load_form_url": "load_clip_form",
                "position": position,
                "error": "Invalid item position",
            }
            return render(request, "partials/item.html", context)

        # Update item with new times
        instance.start_time = seconds2hms(new_start)
        instance.end_time = seconds2hms(new_end)

        # Update name if provided
        name = request.POST.get("name")
        if name:
            instance.name = name

        instance.save()
    else:
        # Form-based update
        form = ClipForm(request.POST, instance=instance)

        if not form.is_valid():
            # Return form with errors
            context = {
                "instance": instance,
                "content": content,
                "can_edit": True,
                "form": form,
                "item_type": "clip",
                "item_type_label": "Clip",
                "update_url": "update_clip",
                "delete_url": "delete_clip",
                "start_seconds": hms2seconds(instance.start_time),
                "end_seconds": hms2seconds(instance.end_time),
            }
            return render(request, "partials/item_form.html", context)

        instance = form.save()

    # Calculate new position
    duration = content.duration
    start_time = hms2seconds(instance.start_time)
    end_time = hms2seconds(instance.end_time)
    start_percent = (start_time / duration * 100) if duration > 0 else 0
    width_percent = ((end_time - start_time) / duration * 100) if duration > 0 else 0

    position = {
        "left": f"{start_percent:.2f}%",
        "width": f"{width_percent:.2f}%",
        "start": start_time,
        "end": end_time,
    }

    # Get JSON from model method
    player_json = json.dumps(content.get_player_json(), indent=2)

    # Render the layer item
    item_html = render_to_string(
        "partials/item.html",
        {
            "instance": instance,
            "content": content,
            "item_type": "clip",
            "update_url": "update_clip",
            "load_form_url": "load_clip_form",
            "position": position,
        },
    )

    # Always render the updated form with OOB swap
    form_content = render_to_string(
        "partials/item_form.html",
        {
            "instance": instance,
            "content": content,
            "can_edit": True,
            "form": ClipForm(instance=instance),
            "item_type": "clip",
            "item_type_label": "Clip",
            "update_url": "update_clip",
            "delete_url": "delete_clip",
            "start_seconds": start_time,
            "end_seconds": end_time,
        },
    )
    # Wrap with OOB swap directive
    form_html = f'<div hx-swap-oob="innerHTML:#detail-form">{form_content}</div>'

    # Render JSON OOB update
    json_html = render_to_string(
        "partials/player_json_oob.html",
        {
            "player_json": player_json,
        },
    )

    # Combine responses
    response = HttpResponse(item_html + form_html + json_html)
    return response


@require_POST
def create_clip(request, annotation_type, content_id):
    """Create a new clip and return updated HTML with JSON OOB."""
    content = get_object_or_404(Content, id=content_id)
    if annotation_type != "clip":
        return HttpResponse("Invalid annotation type", status=400)

    # Check permissions
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    # Get start and end times from POST
    start_time = float(request.POST.get("start_time", 0))
    end_time = float(request.POST.get("end_time", 10))

    # Validate times
    duration = content.duration
    if start_time < 0 or end_time > duration or start_time >= end_time:
        return HttpResponse("Invalid item times", status=400)

    # Get resource from content's file
    if not content.file or not content.file.resource:
        return HttpResponse("Content has no associated resource", status=400)

    # Create new item
    new_item = Clip.objects.create(
        resource=content.file.resource,
        owner=request.user,
        name=f"Clip {content.clips.count() + 1}",
        start_time=seconds2hms(start_time),
        end_time=seconds2hms(end_time),
    )

    # Add to content
    content.clips.add(new_item)
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

    # Get JSON from model method
    player_json = json.dumps(content.get_player_json(), indent=2)

    # Render the new layer item
    item_html = render_to_string(
        "partials/item.html",
        {
            "instance": new_item,
            "content": content,
            "item_type": "clip",
            "update_url": "update_clip",
            "load_form_url": "load_clip_form",
            "position": position,
        },
    )

    # Render JSON OOB update
    json_html = render_to_string(
        "partials/player_json_oob.html",
        {
            "player_json": player_json,
        },
    )

    # Combine responses with OOB swap for layer-items
    response = HttpResponse(
        f'<div hx-swap-oob="beforeend:.layer-items">{item_html}</div>{json_html}'
    )
    return response


@require_http_methods(["DELETE"])
def delete_clip(request, item_type, clip_id):
    """Delete or remove clip from content and return updated JSON OOB."""
    if item_type != "clip":
        return HttpResponse("Invalid item type", status=400)

    instance = get_object_or_404(Clip, id=clip_id)

    # Get content from query/body parameter
    content_id = request.GET.get("content_id") or request.POST.get("content_id")
    if not content_id:
        return HttpResponse("Missing content_id parameter", status=400)

    content = get_object_or_404(Content, id=content_id)

    # Check permissions
    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    try:
        if instance.can_edit(request.user):
            # User owns the item, can fully delete it
            instance.delete()
        else:
            # User doesn't own it, just remove from their content
            content.clips.remove(instance)
            content.save()

        # Get JSON from model method
        player_json = json.dumps(content.get_player_json(), indent=2)

        # Render JSON OOB update
        json_html = render_to_string(
            "partials/player_json_oob.html",
            {
                "player_json": player_json,
            },
        )

        # Render placeholder for form with OOB swap
        form_placeholder = render_to_string("partials/item_form_placeholder.html")
        form_html = (
            f'<div hx-swap-oob="innerHTML:#detail-form">{form_placeholder}</div>'
        )

        response = HttpResponse(json_html + form_html)
        return response
    except Exception as e:
        logger.error(f"Error deleting item {clip_id}: {e}")
        return HttpResponseServerError()
