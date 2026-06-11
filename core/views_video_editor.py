from functools import cmp_to_key
import json
import logging
from urllib.parse import quote

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseNotFound
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
from .models import Subtitle
from .models import Track
from .models import User
from .utils import VTTCue
from .utils import build_vtt_file_string_from_cues
from .utils import convert_srt_content_to_vtt
from .utils import generate_vtt_cues_from_file_path
from .utils import nudge_cue_times

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


def get_annotation_groups(annotations):
    # [{type:"", type_display:"", icon:"", instances: []}]

    groups = {}
    for type_name in ANNOTATION_MODELS.keys():
        type_icon = ANNOTATION_ICONS[type_name]
        new_group = {
            "type": type_name,
            "type_display": type_name.capitalize(),
            "icon": type_icon,
            "instances": [],
        }
        groups[type_name] = new_group

    if type(annotations) == list:
        for annotation in annotations:
            groups[annotation.annotation_type]["instances"].append(annotation)

    return groups.values()


def build_annotation_panel(request, annotation_set_id):
    try:
        annotation_set = get_object_or_404(AnnotationSet, pk=annotation_set_id)
        annotations = annotation_set.get_active_annotations_from_tracks()
        annotation_groups = get_annotation_groups(annotations)
        annotation_panel_html = render_to_string(
            "core/partials/annotation_panel.html",
            {"annotation_groups": annotation_groups},
            request,
        )
    except Exception as e:
        logger.error(f"Failed to build annotation panel. Exception: {e}")
        return HttpResponseServerError()

    return HttpResponse(annotation_panel_html)


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
    try:
        content = Content.objects.get(pk=content_id)

        # Check if user can view this content
        if not request.user.can_view_content(content):
            return HttpResponse("Unauthorized", status=403)

        # Get file key for video streaming
        file_key = request.user.get_resource_filekey(content)

        subtitle_options = content.get_subtitles()

        # Get available annotation sets
        available_sets = content.get_available_annotation_sets()

        # Determine if user can edit the active annotation set
        annotation_set = content.annotation_set
        can_edit = (
            annotation_set.can_edit(request.user)
            if annotation_set is not None
            else True
        )

        can_edit_annotation_set = annotation_set is not None and (
            annotation_set.owner == request.user or request.user.is_admin
        )

        # Prepare track data for timeline
        tracks = annotation_set.get_tracks() if annotation_set is not None else []

        annotations = (
            annotation_set.get_active_annotations_from_tracks()
            if annotation_set is not None
            else []
        )

        annotation_groups = (
            get_annotation_groups(annotations) if annotation_set is not None else []
        )

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
            "subtitle_options": subtitle_options,
        }

        return render(request, "core/video_editor.html", context)
    except Content.DoesNotExist:
        logger.error("Failed to load video editor: missing content.")
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to load video editor. Exception: {e}")
        return HttpResponseServerError()


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
    try:
        content = Content.objects.get(pk=content_id)

        # Check permissions
        if not request.user.can_view_content(content):
            return HttpResponse("Unauthorized", status=403)

        annotation_set_id = body["annotation_set_id"]

        if not annotation_set_id:
            logger.error(f"Annotation set id was not provided: {annotation_set_id}")
            return HttpResponseBadRequest()

        annotation_set = AnnotationSet.objects.get(pk=annotation_set_id)
        if not annotation_set.can_be_viewed_by(request.user):
            return HttpResponse("Unauthorized", status=403)

        content.annotation_set = annotation_set
        content.save()

        return HttpResponse()
    except Content.DoesNotExist:
        logger.error(
            f"Failed to update selected annotation set because Content does not exist. Content id: {content_id}"
        )
        return HttpResponseBadRequest()
    except AnnotationSet.DoesNotExist:
        logger.error(
            f"Failed to update selected annotation set becuase AnnotationSet does not exist. AnnotationSet id: {annotation_set_id}"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to update selected annotation set. Exception: {e}")
        return HttpResponseServerError()


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
def add_editor_to_annotation_set(request):
    """Add a user as an editor to an AnnotationSet."""
    try:
        parsed_body = json.loads(request.body)
        if "annotation_set_id" not in parsed_body or "editor_id" not in parsed_body:
            logger.error(
                "Failed to add editor to annotaion set; missing annotation_set_id and/or editor_id"
            )
            return HttpResponseBadRequest()

        annotation_set_id = parsed_body["annotation_set_id"]
        annotation_set = AnnotationSet.objects.get(pk=annotation_set_id)

        # Only owner can add editors
        if request.user != annotation_set.owner:
            return HttpResponse("Unauthorized", status=403)

        user_id = parsed_body["editor_id"]
        user = User.objects.get(pk=user_id)

        annotation_set.editors.add(user)

        form_html = render_to_string(
            "core/partials/annotation_set_selected_editors.html",
            {"annotation_set": annotation_set},
            request=request,
        )

        return HttpResponse(form_html)
    except AnnotationSet.DoesNotExist:
        logger.error(
            "Failed to add editor to annotation set because the set doesn't exist"
        )
        return HttpResponseBadRequest()
    except User.DoesNotExist:
        logger.error(
            "Failed to add editor to annotation set because the editor doesn't exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to add editor to annotation set. Exception: {e}")
        return HttpResponseServerError()


@require_POST
@login_required
def search_for_editor(request):
    try:
        parsed_body = json.loads(request.body)
        if "search_string" not in parsed_body:
            logger.error("Failed to search for editors; missing search string")
            return HttpResponseBadRequest()
        query = parsed_body["search_string"].strip()
        editor_results = User.objects.filter(
            (
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(username__icontains=query)
            )
            & ~Q(id=request.user.id)
        ).order_by("last_name")[:25]
        result_html = render_to_string(
            "partials/editor_search_results.html",
            {"editor_results": editor_results},
            request,
        )
        return HttpResponse(result_html)
    except Exception as e:
        logger.error(f"Failed to search for editors. Exception: {e}")
        return HttpResponseServerError()


@require_http_methods(["DELETE"])
@login_required
def remove_editor_from_annotation_set(request, annotation_set_id, user_id):
    """Remove a user as an editor from an AnnotationSet."""
    try:
        annotation_set = AnnotationSet.objects.get(pk=annotation_set_id)

        # Only owner can remove editors
        if request.user != annotation_set.owner:
            return HttpResponse("Unauthorized", status=403)

        user = User.objects.get(pk=user_id)
        annotation_set.editors.remove(user)

        return HttpResponse()
    except AnnotationSet.DoesNotExist:
        logger.error(
            f"Failed to remove user from annotation set because the annotation set doesn't exist. AnnotationSet id: {annotation_set_id}"
        )
        return HttpResponseBadRequest()
    except User.DoesNotExist:
        logger.error(
            f"Failed to remove user from annotation set because the User does not exist. User id: {user_id}"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to remove user from annotation set. Exception: {e}")
        return HttpResponseServerError()


@require_POST
@login_required
@transaction.atomic
def create_annotation(request, annotation_type, track_id):
    """Create annotation in the active AnnotationSet."""

    track = get_object_or_404(Track, id=track_id)

    if not track.annotation_set.can_edit(request.user):
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
        "track": track,
        "name": f"{annotation_type.title()} {model_class.objects.filter(track=track).count() + 1}",
        "start_time": start_time,
        "active": True,
        "prev": None,
        "next": None,
    }

    if annotation_type != "pause":
        data["end_time"] = end_time

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
    track_item_html = render_to_string(
        "partials/item.html",
        {"item": annotation},
        request=request,
    )

    panel_item_html = render_to_string(
        "partials/annotation_list_item.html",
        {"id": annotation.pk, "type": annotation_type, "name": annotation.name},
    )

    return JsonResponse(
        {"track_item_html": track_item_html, "panel_item_html": panel_item_html}
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

    if not annotation.track.annotation_set.can_edit(user):
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


def generate_censor_item_and_positions_html(parent_annotation_id):
    try:
        parent_annotation = BlurAnnotation.objects.get(pk=parent_annotation_id)
    except Exception as e:
        logger.error(
            f"Failed to get parent_annotation while updateing censor positions html. Exception: {e}"
        )
        return False

    try:
        censor_positions = get_list_of_blur_annotation_positions(parent_annotation)
        censor_positions_html = render_to_string(
            "partials/censor_positions.html", {"item_positions": censor_positions}
        )
        track_item_html = render_to_string(
            "partials/item.html", {"item": parent_annotation}
        )
        return {"censorPositions": censor_positions_html, "trackItem": track_item_html}

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

    item_and_position_html = generate_censor_item_and_positions_html(
        parent_annotation_id
    )
    if item_and_position_html is False:
        return HttpResponseServerError()
    return JsonResponse(item_and_position_html)


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
            item_and_positions_html = generate_censor_item_and_positions_html(
                this_blur_position.blur_annotation.pk
            )
            if item_and_positions_html is False:
                return HttpResponseServerError()
            return JsonResponse(item_and_positions_html)
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
        return HttpResponse()


def generate_annotation_updated_html(
    request, content, annotation, annotation_type, position
):
    item_html = render_to_string(
        "partials/item.html",
        {"item": annotation},
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


@require_POST
@login_required
@transaction.atomic
def update_annotation(request, annotation_type, annotation_id):
    try:
        json_data = json.loads(request.body)
        if "content_id" not in json_data or "start_time" not in json_data:
            return HttpResponseBadRequest()

        content_id = json_data["content_id"]
        content = Content.objects.get(pk=content_id)

        validation_result = validate_annotation_update_request(
            request.user, content, annotation_type, annotation_id
        )
        if not validation_result["success"]:
            return validation_result["result"]
        annotation = validation_result["result"]

        fields_to_update = {}
        fields_to_update["start_time"] = json_data["start_time"]
        if "annotation_name" in json_data:
            fields_to_update["name"] = json_data["annotation_name"]

        if "track_id" in json_data and json_data["track_id"] is not None:
            try:
                new_track = Track.objects.get(pk=json_data["track_id"])
                fields_to_update["track"] = new_track
            except Track.DoesNotExist:
                logger.error(
                    "Could not transfer annotation to track because it does not exist"
                )
            except Exception as e:
                logger.error(
                    f"Failed to update annotation's parent track. Exception: {e}"
                )

        if "description" in json_data:
            fields_to_update["description"] = json_data["description"]

        if annotation_type != "pause":
            fields_to_update["end_time"] = json_data["end_time"]

        if (
            annotation_type == "pause" or annotation_type == "skip"
        ) and "message" in json_data:
            fields_to_update["message"] = json_data["message"]

        if annotation_type == "blank" and "blank_type" in json_data:
            fields_to_update["type"] = json_data["blank_type"]

        if annotation_type == "comment":
            if "text" in json_data:
                fields_to_update["text"] = json_data["text"]
            if "top_left_x" in json_data and json_data["top_left_x"] is not None:
                fields_to_update["top_left_x"] = float(json_data["top_left_x"])
            if "top_left_y" in json_data and json_data["top_left_y"] is not None:
                fields_to_update["top_left_y"] = float(json_data["top_left_y"])
            if (
                "bottom_right_x" in json_data
                and json_data["bottom_right_x"] is not None
            ):
                fields_to_update["bottom_right_x"] = float(json_data["bottom_right_x"])
            if (
                "bottom_right_y" in json_data
                and json_data["bottom_right_y"] is not None
            ):
                fields_to_update["bottom_right_y"] = float(json_data["bottom_right_y"])
            if (
                "font_size_in_rem" in json_data
                and json_data["font_size_in_rem"] is not None
            ):
                fields_to_update["font_size_in_rem"] = float(
                    json_data["font_size_in_rem"]
                )
            if "font_color" in json_data:
                fields_to_update["font_color"] = json_data["font_color"]

        for key, value in fields_to_update.items():
            setattr(annotation, key, value)

        annotation.save()
        if annotation_type == "censor":
            annotation.remove_positions_outside_of_timebox()
        annotation.refresh_from_db()

        new_start_time = annotation.start_time
        new_end_time = getattr(annotation, "end_time", new_start_time)
        position = {"start": new_start_time, "end": new_end_time}

        new_annotation_html = generate_annotation_updated_html(
            request, content, annotation, annotation_type, position
        )
        item_html = new_annotation_html["item_html"]
        form_html = new_annotation_html["form_html"]

        return JsonResponse({"item_html": item_html, "form_html": form_html})
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
    if not annotation.track.annotation_set.can_edit(request.user):
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

    if not annotation.track.annotation_set.can_edit(request.user):
        return HttpResponse(
            "You don't have permission to edit this annotation", status=403
        )

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
def update_annotation_set_name(request):
    try:
        parsed_body = json.loads(request.body)
        if "annotation_set_id" not in parsed_body:
            logger.error(
                "Failed to update annotation set name due to missing annotation_set_id value"
            )
            return HttpResponseBadRequest()
        if "name" not in parsed_body:
            logger.error("Failed to update annotation set name; missing name value.")
            return HttpResponseBadRequest()

        annotation_set_id = parsed_body["annotation_set_id"]
        annotation_set = AnnotationSet.objects.get(pk=annotation_set_id)
        if not (request.user == annotation_set.owner or request.user.is_admin):
            return HttpResponse("Unauthorized", status=403)

        name = parsed_body["name"].strip()
        if name:
            annotation_set.name = name
            annotation_set.save()

        return HttpResponse()
    except AnnotationSet.DoesNotExist:
        logger.error(
            f"Failed to update annotation set name; unknown AnnotationSet. id: {annotation_set_id}"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to update annotation set name. Exception: {e}")
        return HttpResponseServerError()


def create_annotation_set(request):
    try:
        parsed_body = json.loads(request.body)
        if "content_id" not in parsed_body:
            logger.error(
                "Failed to create new annotation set because content_id is not defined"
            )
            return HttpResponseBadRequest()
        content = Content.objects.get(pk=parsed_body["content_id"])
        name = parsed_body.get("name")
        annotation_set_json = parsed_body.get("annotation_set_json", None)
        annotation_set_id_to_copy = parsed_body.get("annotation_set_id_to_copy", None)

        if annotation_set_json is not None:
            try:
                json.loads(annotation_set_json)
            except (json.JSONDecodeError, TypeError):
                logger.error(
                    "Failed to create new annotation set because annotations_json is not valid JSON"
                )
                return HttpResponseBadRequest()

        annotation_set = AnnotationSet.create_for_content(
            content,
            request.user,
            set_name=name,
            annotation_set_json=annotation_set_json,
            annotation_set_id_to_copy=annotation_set_id_to_copy,
        )

        if annotation_set is None:
            return HttpResponseServerError()

        content.annotation_set = annotation_set
        content.save()

        return HttpResponse()

    except Exception as e:
        logger.error(f"Failed to create new annotation set. Exception: {e}")
        return HttpResponseServerError()


def export_annotation_set(request, annotation_set_id):
    """Gets all annotations in the set as a JSON object and allows it to be downloaded
    via the Content-Disposition: attachment HTTP response header. UTF-8 characters are
    allowed in the filename in case non-ASCII/non-english characters are used. Note:
    no file is created from this request, the JSON data is made available as if it was
    a file and is downloaded by the client's browser."""
    try:
        annotation_set = AnnotationSet.objects.get(pk=annotation_set_id)
        annotations = annotation_set.to_player_json()
        response = HttpResponse(
            json.dumps(annotations, indent=2),
            content_type="application/json",
        )
        filename = quote(f"{annotation_set.name}.json")
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
        return response

    except AnnotationSet.DoesNotExist:
        logger.error("Failed to export annotation set because it does not exist")
        return HttpResponseNotFound()
    except Exception as e:
        logger.error(f"Failed to export annotation set. Exception: {e}")
        return HttpResponseServerError()


def delete_annotation_set(request, annotation_set_id):
    if annotation_set_id is None:
        return HttpResponseBadRequest()
    try:
        annotation_set = AnnotationSet.objects.get(pk=annotation_set_id)
        annotation_set.delete()

        return HttpResponse()
    except AnnotationSet.DoesNotExist:
        logger.error("Failed to delete annotation set because it doesn't exist.")
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to delete annotation set. Exception: {e}")
        return HttpResponseServerError()


def display_annotation_set_create_option(request):
    try:
        return HttpResponse(
            render_to_string(
                "partials/annotation_set_options/create_new.html", {}, request
            )
        )
    except Exception as e:
        logger.error(
            f"Failed to generate create annotation set option template. Exception: {e}"
        )
        return HttpResponseServerError()


def display_annotation_set_import_option(request):
    try:
        return HttpResponse(
            render_to_string(
                "partials/annotation_set_options/import_from_file.html", {}, request
            )
        )
    except Exception as e:
        logger.error(
            f"Failed to return Annotation Set import option template. Exception: {e}"
        )
        return HttpResponseServerError()


def display_copy_from_annotation_set_option(request, content_id):
    try:
        content = Content.objects.get(pk=content_id)
        available_sets = content.get_available_annotation_sets()
        return HttpResponse(
            render_to_string(
                "partials/annotation_set_options/copy_from_set.html",
                {"available_annotation_sets": available_sets, "can_edit": True},
                request,
            )
        )

    except Content.DoesNotExist:
        logger.error("Failed to retrieve content because it does not exist.")
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(
            f"Failed to return Copy from Annotation Set option template. Exception: {e}"
        )
        return HttpResponseServerError()


def display_use_existing_annotation_set_option(request, content_id):
    try:
        content = Content.objects.get(pk=content_id)
        available_sets = content.get_available_annotation_sets()
        return HttpResponse(
            render_to_string(
                "partials/annotation_set_options/use_existing_set.html",
                {
                    "available_annotation_sets": available_sets,
                    "can_edit": (
                        content.collection.owner == request.user
                        or request.user.is_admin
                    ),
                },
                request,
            )
        )

    except Content.DoesNotExist:
        logger.error(
            "Failed to return Annotation sets associated with this content because the content does not exist"
        )
        return HttpResponseBadRequest()

    except Exception as e:
        logger.error(
            f"Failed to return Use existing Annotation Set option template. Exception: {e}"
        )
        return HttpResponseServerError()


@login_required
@require_POST
def update_track(request):
    """Edit track name by changing the track_name attribute of all associated annotations"""
    try:
        parsed_data = json.loads(request.body)
        track_id = parsed_data["track_id"]
        track = Track.objects.get(pk=track_id)
    except Track.DoesNotExist:
        logger.error("Failed to get track object because it does not exist.")
        return HttpResponse(status=404)
    except Exception as e:
        logger.error(f"Failed to get track object. Exception: {e}")
        return HttpResponseServerError()

    try:
        if "new_track_name" in parsed_data:
            track.name = parsed_data["new_track_name"]
        if "new_stack_position" in parsed_data:
            track.stack_position = parsed_data["new_stack_position"]
        track.save()
    except Exception as e:
        logger.error(f"Failed to update track name. Exception: {e}")
        return HttpResponseServerError()

    track_html = render_to_string(
        "core/partials/timeline-track-row.html", {"track": track}, request
    )

    return HttpResponse(track_html)


def convertTracksToHTML(annotation_set, request):
    tracks = annotation_set.get_tracks()
    tracks_html = []
    for track in tracks:
        tracks_html.append(
            render_to_string(
                "core/partials/timeline-track-row.html", {"track": track}, request
            )
        )

    return JsonResponse({"tracks_html": tracks_html})


@login_required
@require_POST
def update_track_positions_in_set(request):
    """Update all track stack positions in an annotation set"""
    parsed_data = json.loads(request.body)
    if "track_ids" not in parsed_data:
        logger.error("Failed to update track positions due to invalid input")
        return HttpResponseBadRequest()

    try:
        annotation_set = Track.objects.get(
            pk=parsed_data["track_ids"][0]
        ).annotation_set
    except Exception as e:
        logger.error(
            f"Failed to get annotation set from track while updating track positions. Exception: {e}"
        )
        return HttpResponseServerError()

    try:
        index = 0
        for track_id in parsed_data["track_ids"]:
            track = Track.objects.get(pk=track_id)
            track.stack_position = index
            track.save()
            index += 1
    except Exception as e:
        logger.error(f"Failed to update track positions. Exception: {e}")
        return HttpResponseServerError()

    return convertTracksToHTML(annotation_set, request)


@login_required
@require_POST
def create_track(request):
    try:
        parsed_body = json.loads(request.body)
        if "annotation_set_id" not in parsed_body:
            logger.error("Failed to create new track due to missing annotation_set_id")
            return HttpResponseBadRequest()

        annotation_set = AnnotationSet.objects.get(pk=parsed_body["annotation_set_id"])
        set_has_tracks = annotation_set.tracks.count() > 0

        track = {
            "annotation_set": annotation_set,
            "stack_position": annotation_set.get_highest_stack_position() + 1
            if set_has_tracks
            else 0,
        }
        if "track_name" in parsed_body:
            track["name"] = parsed_body["track_name"]

        new_track = Track.objects.create(**track)

        return convertTracksToHTML(annotation_set, request)
    except Exception as e:
        logger.error(f"Failed to create a new track. Exception: {e}")
        return HttpResponseServerError()


@login_required
def delete_track(request, track_id):
    if request.method != "DELETE":
        return HttpResponseBadRequest()
    try:
        track = Track.objects.get(pk=track_id)
        if track.stack_position == 0:
            logger.error(
                f"Request to delete primary track ignored. Track id: {track_id}"
            )
            return HttpResponseBadRequest()
        track.delete()
        return HttpResponse()
    except Track.DoesNotExist:
        logger.error("Failed to delete track because it does not exist.")
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Failed to delete track. Exception: {e}")
        return HttpResponseServerError()


@require_GET
def get_editable_subtitles(request, subtitle_id):
    try:
        subtitle_obj = Subtitle.objects.get(pk=subtitle_id)
        cues = generate_vtt_cues_from_file_path(subtitle_obj.subtitles_file.path)
        return HttpResponse(
            render_to_string(
                "partials/subtitle_panel_content.html",
                {"subtitle_track": subtitle_obj, "cues": cues},
            )
        )

    except Subtitle.DoesNotExist:
        logger.error("Failed to generate subtitle html: missing Subtitle object.")
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Error generating html cues from file path. Exception: {e}")
        return HttpResponseServerError()


@require_POST
def create_subtitle(request):
    form = SubtitleForm(request.POST, request.FILES)
    if form.is_valid():
        data = form.cleaned_data
        uploaded_file = request.FILES["subtitles_file"]
        if uploaded_file is None:
            logger.error("No subtitle file provided.")
            return HttpResponseBadRequest()

        # automatically convert .srt files to .vtt
        uploaded_file_content = convert_srt_content_to_vtt(
            uploaded_file.read().decode("utf-8")
        )

        # create new subtitle object
        try:
            new_subtitle = Subtitle.objects.create(
                resource=data["resource"],
                owner=data["owner"],
                language=data["language"],
                name=data["name"],
                subtitles_file=ContentFile(
                    content=uploaded_file_content, name=uploaded_file.name
                ),
                is_original=data["is_original"],
            )
        except Exception as e:
            logger.error(
                f"Error creating Subtitles object. data: {data}. Exception: {e}"
            )
            return HttpResponseServerError()
    else:
        return HttpResponseBadRequest()

    return render(
        request, "partials/subtitle_track.html", {"subtitle_track": new_subtitle}
    )


@require_POST
def update_subtitle_metadata(request):
    form = SubtitleForm(request.POST, request.FILES)
    if form.is_valid():
        data = form.cleaned_data

        uploaded_file = request.FILES["subtitles_file"]

        if uploaded_file is not None:
            uploaded_file_content = convert_srt_content_to_vtt(
                uploaded_file.read().decode("utf-8")
            )
            uploaded_file_name = uploaded_file.name

        try:
            if "subtitle_id" in request.POST:
                subtitle_obj = get_object_or_404(
                    Subtitle, id=request.POST.get("subtitle_id")
                )
            else:
                return HttpResponseBadRequest()

            if "language" in data:
                subtitle_obj.language = data["language"]
            if "name" in data:
                subtitle_obj.name = data["name"]
            if uploaded_file is not None:
                subtitle_obj.subtitles_file = ContentFile(
                    content=uploaded_file_content, name=uploaded_file_name
                )
            if "is_original" in data:
                subtitle_obj.is_original = data["is_original"]
            if "words" in request.POST:
                subtitle_obj.words = data["words"]
            subtitle_obj.save()

            # remove temp file when main file is updated
            # this is not included where subtitles_file is set because we want
            # to ensure we don't over write the temp file unless the main file
            # is successfully updated.
            if uploaded_file is not None:
                subtitle_obj.subtitles_temp_file = None
                subtitle_obj.save()

            return render(
                request,
                "partials/subtitle_track.html",
                {"subtitle_track": subtitle_obj},
            )
        except Exception as e:
            logger.error(
                f"Error while updating subtitle object with id: {request.POST.get('subtitle_id')}. Exception: {e}"
            )
            return HttpResponseServerError()
    else:
        return HttpResponseBadRequest()


@require_POST
def update_subtitle_content(request):
    try:
        # build VTTCue list
        request_data = json.loads(request.body)
        subtitle_id = request_data["subtitle_id"]
        dict_cue_list = request_data["cues"]
        cues_list: list[VTTCue] = []
        for dict_cue in dict_cue_list:
            new_cue = VTTCue()
            new_cue.from_json_dict(dict_cue)
            cues_list.append(new_cue)

        def compare_cues(cueA, cueB):
            return cueA.start_time - cueB.start_time

        cues_list.sort(key=cmp_to_key(compare_cues))

        # change timing of cues if applicable
        seconds_nudge = request_data["seconds_nudge"]
        nudge_excluded_cues = request_data["nudge_excluded_cues"]
        if seconds_nudge:
            nudge_cue_times(cues_list, nudge_excluded_cues, seconds_nudge)

        # build and save new vtt file
        new_vtt_string = build_vtt_file_string_from_cues(cues_list)
        subtitle_obj = Subtitle.objects.get(pk=subtitle_id)
        is_autosave = request_data["is_autosave"]

        # something is giong on here where part of the path name is being duplicated
        # files are saved as media/filename where in this case, filename is something like
        # "resource name/subtitles/original filename"
        # but the model is set to attach the current filename in place of "original filename"
        # so we end up with "resource name/subtitles/resource name/subtitles/.../intended filename"
        if is_autosave:
            with subtitle_obj.subtitles_temp_file.open("w") as f:
                f.write(new_vtt_string)
        else:
            with subtitle_obj.subtitles_file.open("w") as f:
                f.write(new_vtt_string)
        subtitle_obj.save()
        return HttpResponse(
            render_to_string(
                "partials/subtitle_cues.html", {"cues": cues_list}, request
            )
        )

    except Subtitle.DoesNotExist:
        logger.error(
            "Failed to update subtitle content: Subtitle object does not exist"
        )
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(f"Error while updating subtitle content: {e}")
        return HttpResponseServerError()


@require_http_methods(["DELETE"])
def delete_subtitle(request, subtitle_id):
    try:
        subtitle_obj = Subtitle.objects.get(pk=subtitle_id)
        subtitle_obj.delete()
        return HttpResponse("", status=200)
    except Subtitle.DoesNotExist:
        logger.error("Failed to delete subtitle: Subtitle object does not exist")
        return HttpResponseBadRequest()
    except Exception as e:
        logger.error(
            f"Error while deleting subtitle object with id: {subtitle_id}. Exception: {e}"
        )
        return HttpResponseServerError()
