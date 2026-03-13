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
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .models import AnnotationSet
from .models import BlankAnnotation
from .models import BlurAnnotation
from .models import BlurAnnotationPosition
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

ANNOTATION_ICONS = {
    "skip": "/static/img/skip-icon.svg",
    "mute": "/static/img/mute-icon.svg",
    "blank": "/static/img/blank-icon.svg",
    "pause": "/static/img/pause-icon.svg",
    "censor": "/static/img/blur-icon.svg",
    "comment": "/static/img/comment-icon.svg",
}


def get_annotation_groups(annotation_set):
    if not annotation_set:
        return []
    # [{type:"", instances: []}]
    groups = []
    for type_name, model_class in ANNOTATION_MODELS.items():
        type_icon = ANNOTATION_ICONS[type_name]
        new_group = {
            "type": type_name,
            "type_display": type_name.capitalize(),
            "icon": type_icon,
            "instances": [],
        }
        annotations = model_class.objects.filter(
            annotation_set=annotation_set, active=True
        ).order_by("start_time")
        for annotation in annotations:
            start_time = annotation.start_time
            end_time = getattr(annotation, "end_time", start_time)
            new_group["instances"].append(
                {
                    "instance": annotation,
                    "id": annotation.pk,
                    "name": annotation.name,
                    "item_type": type_name,
                    "track": annotation.track_name,
                    "position": {
                        "start": start_time,
                        "end": end_time,
                    },
                }
            )
        groups.append(new_group)

    return groups


def build_annotation_panel(request, annotation_set_id):
    try:
        annotation_set = get_object_or_404(AnnotationSet, pk=annotation_set_id)
        annotation_groups = get_annotation_groups(annotation_set)
        annotation_panel_html = render_to_string(
            "core/partials/annotation_panel.html",
            {"annotation_groups": annotation_groups},
            request,
        )
    except Exception as e:
        logger.error(f"Failed to build annotation panel. Exception: {e}")
        return HttpResponseServerError()

    return HttpResponse(annotation_panel_html)


def build_editor_tracks(annotation_set_id):
    try:
        annotation_set = AnnotationSet.objects.get(pk=annotation_set_id)
        annotation_groups = get_annotation_groups(annotation_set)
        tracks = {}
        for group in annotation_groups:
            for instance in group["instances"]:
                track_name = instance["track"]
                if track_name not in tracks:
                    tracks[track_name] = {"display_name": track_name, "items": []}
                tracks[track_name]["items"].append(instance)
        return tracks
    except Exception as e:
        logger.error(f"Failed to build editor tracks. Exception: {e}")
        return False


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
    tracks = build_editor_tracks(annotation_set.id)
    if tracks == False:
        return HttpResponseServerError()

    annotation_groups = get_annotation_groups(annotation_set)

    context = {
        "content": content,
        "content_id": content_id,
        "file_key": file_key.id if file_key else None,
        "allow_events": True,
        "available_annotation_sets": available_sets,
        "annotation_set": annotation_set,
        "can_edit": can_edit,
        "can_edit_annotation_set": can_edit_annotation_set,
        "annotation_groups": annotation_groups,
        "tracks": tracks,
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
def get_player_wrapper_html(request):
    """Generate the HTML for the video player. This endpoint is intended to be used when
    an annotation is successfully created, deleted, or updated"""
    body = json.loads(request.body.decode("utf-8"))
    content_id = body["content_id"]
    content = get_object_or_404(Content, pk=content_id)

    if not request.user.can_view_content(content):
        return HttpResponse("Unauthorized", status=403)

    try:
        video_html = render_to_string(
            "partials/player-wrapper.html",
            {
                "content_id": content_id,
                "resource_file_key_id": request.user.get_resource_filekey(content).pk,
            },
            request=request,
        )

        return HttpResponse(video_html, status=200)
    except Exception as e:
        logger.error(f"Could not render player-wrapper.html. Exception: {e}")
        return HttpResponseServerError()


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
    # layer_results = build_annotation_layers(content, annotation_set, can_edit)
    # layers = layer_results["layers"]

    # # Render timeline using shared partial
    # timeline_layers_html = render_to_string(
    #     "core/partials/timeline_layers.html",
    #     {"layers": layers, "content_id": content_id},
    #     request=request,
    # )

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
        # {"video_section": video_html, "timeline_layers": timeline_layers_html}
        {"video_section": video_html}
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

    parsed_body = json.loads(request.body)
    start_time = float(parsed_body["start_time"])
    end_time = (
        float(parsed_body["end_time"]) if annotation_type != "pause" else start_time
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

    if annotation_type == "comment":
        data.update({"text": "", "x": 50.0, "y": 50.0})
    elif annotation_type == "pause":
        data["message"] = ""
    elif annotation_type == "blank":
        data["type"] = "k"

    annotation = model_class.objects.create(**data)
    if annotation_type == "censor":
        BlurAnnotationPosition.objects.create(
            blur_annotation=annotation, time=0, x=50, y=50, width=4, height=3
        )

    # Calculate position
    position = {
        "start": start_time,
        "end": end_time,
    }

    # Render item using shared partial
    layer_item_html = render_to_string(
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

    panel_item_html = render_to_string(
        "partials/annotation_list_item.html",
        {"id": annotation.pk, "type": annotation_type, "name": annotation.name},
    )

    return JsonResponse(
        {"layer_item_html": layer_item_html, "panel_item_html": panel_item_html}
    )


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


def get_list_of_blur_annotation_positions(blur_annotation_parent):
    try:
        censor_positions = list(
            BlurAnnotationPosition.objects.filter(
                blur_annotation=blur_annotation_parent
            ).order_by("time")
        )
    except Exception as e:
        logger.error(f"Failed to get censor positions. Exception: {e}")
        return []
    return censor_positions


def generate_censor_positions_html(parent_annotation_id):
    try:
        parent_annotation = BlurAnnotation.objects.get(pk=parent_annotation_id)
    except Exception as e:
        logger.error(
            f"Failed to get parent_annoation while updateing censor positions html. Exception: {e}"
        )
        return False

    try:
        censor_positions = get_list_of_blur_annotation_positions(parent_annotation)
        censor_positions_html = render_to_string(
            "partials/censor_positions.html", {"item_positions": censor_positions}
        )
        return censor_positions_html

    except Exception as e:
        logger.error(f"Failed to generate censor_postion html. Exception: {e}")
        return False


@require_POST
def create_censor_position(request):
    try:
        parsed_body = json.loads(request.body)
        parent_annotation_id = parsed_body["parent_annotation_id"]
        position_time = parsed_body["time"]
        position_x = parsed_body["x"]
        position_y = parsed_body["y"]
        position_width = parsed_body["width"]
        position_height = parsed_body["height"]
    except Exception as e:
        logger.error(
            f"Unable to parse data for updating or creating censor positions: {e}"
        )
        return HttpResponseBadRequest()

    if (
        not parent_annotation_id
        or not position_time
        or not position_x
        or not position_y
        or not position_width
        or not position_height
    ):
        return HttpResponseBadRequest()

    # check if the position already exists
    try:
        num_of_pre_existing_objs = BlurAnnotationPosition.objects.filter(
            blur_annotation__pk=parent_annotation_id, time=position_time
        ).count()
        if num_of_pre_existing_objs > 0:
            return HttpResponse(status=200)
    except Exception as e:
        logger.error(f"Failed to query BlurAnnotationPositions. Exception: {e}")
        return HttpResponseServerError()

    try:
        parent_annotation = get_object_or_404(BlurAnnotation, pk=parent_annotation_id)
        if parent_annotation.end_time < round(float(position_time), 2):
            return HttpResponseBadRequest(
                "New censor position cannot occur at a time greater than the blur annotation's end time"
            )
        elif parent_annotation.start_time > round(float(position_time), 2):
            return HttpResponseBadRequest(
                "New censor position cannot occur before the start time of the parent censor annotation"
            )
        BlurAnnotationPosition.objects.create(
            blur_annotation=parent_annotation,
            time=position_time,
            x=position_x,
            y=position_y,
            width=position_width,
            height=position_height,
        )
    except Exception as e:
        logger.error(f"Failed to create new BlurAnnotationPosition. Exception: {e}")
        return HttpResponseServerError()

    censor_position_html = generate_censor_positions_html(parent_annotation_id)
    if censor_position_html == False:
        return HttpResponseServerError()
    return HttpResponse(censor_position_html, status=201)


@require_POST
def update_censor_position(request):
    try:
        parsed_body = json.loads(request.body)
        position_id = parsed_body["position_id"]
        position_time = parsed_body["time"]
        position_x = parsed_body["x"]
        position_y = parsed_body["y"]
        position_height = parsed_body["height"]
        position_width = parsed_body["width"]
    except Exception as e:
        logger.error(
            f"Unable to parse data for updating or creating censor positions: {e}"
        )
        return HttpResponseBadRequest()

    # update the existing BlurAnnotationPosition
    else:
        try:
            this_blur_position = BlurAnnotationPosition.objects.get(pk=position_id)
            this_blur_position.time = position_time
            this_blur_position.x = position_x
            this_blur_position.y = position_y
            this_blur_position.height = position_height
            this_blur_position.width = position_width
            this_blur_position.save()
            censor_position_html = generate_censor_positions_html(
                this_blur_position.blur_annotation.pk
            )
            if censor_position_html == False:
                return HttpResponseServerError()
            return HttpResponse(censor_position_html, status=201)
        except Exception as e:
            logger.error(f"Unable to update pre-existing BlurAnnotationPosition: {e}")
            return HttpResponseServerError()


def delete_censor_position(request, position_id):
    try:
        position = BlurAnnotationPosition.objects.get(pk=position_id)
        # I know this looks dumb, but if i used position.blur_annotation to get the parent,
        # the reference to that parent is deleted once position is deleted. I do this to
        # allow for access to the parent after the position is deleted
        blur_annotation_parent = BlurAnnotation.objects.get(
            pk=position.blur_annotation.pk
        )
    except Exception as e:
        logger.error(
            f"Failed to get blur annotation parent while deleting annotation. Exception: {e}"
        )

    try:
        if position.time > 0:
            position.delete()
        else:
            return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to delete blur position. Exception: {e}")
        return HttpResponseServerError()

    if not blur_annotation_parent:
        return HttpResponse(status=205)
    else:
        censor_position_html = generate_censor_positions_html(blur_annotation_parent.pk)
        return HttpResponse(censor_position_html, status=200)


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

    item_positions = []
    if annotation_type == "censor":
        try:
            item_positions = get_list_of_blur_annotation_positions(annotation)
        except Exception as e:
            logger.error(f"Failed to get blur annotation positions. Exception: {e}")

    form_html = render_to_string(
        "core/partials/annotation_form.html",
        {
            "item_type": annotation_type,
            "instance": annotation,
            "item_type_label": annotation_type.title(),
            "content": content,
            "can_edit": True,
            "start_seconds": position["start"],
            "end_seconds": position["end"],
            "item_positions": item_positions,
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
    update_fields["name"] = parsed_body["annotation_name"]
    update_fields["start_time"] = parsed_body["start_time"]
    update_fields["description"] = parsed_body["description"]

    if annotation_type == "pause" or annotation_type == "skip":
        update_fields["message"] = parsed_body["message"]

    if annotation_type != "pause":
        update_fields["end_time"] = parsed_body["end_time"]
    elif annotation_type == "comment":
        update_fields["text"] = parsed_body["text"]
        x = parsed_body["x"]
        y = parsed_body["y"]
        if x is not None:
            update_fields["x"] = float(x)
        if y is not None:
            update_fields["y"] = float(y)
    elif annotation_type == "blank":
        update_fields["type"] = parsed_body["blank_type"]

    for field, value in update_fields.items():
        setattr(annotation, field, value)

    try:
        annotation.save()
    except Exception as e:
        logger.error(f"Unable to save annotation: {e}")
        return HttpResponseServerError()

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

    html_result = generate_annotation_updated_html(
        request,
        content,
        annotation,
        annotation_type.lower(),
        {"start": start_seconds, "end": end_seconds},
    )

    form_html = html_result["form_html"]

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
