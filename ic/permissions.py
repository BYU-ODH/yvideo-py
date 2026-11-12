"""Permission decorators for International Cinema views, in core.permissions' style.

Each resolves the object named in the URL, checks one predicate, and hands the object to
the view, and records `permission_check` for the URL-registry test.
"""

from functools import wraps

from django.shortcuts import get_object_or_404

from core.models import Resource
from core.permissions import _object_permission_required
from core.permissions import forbidden

from .models import ICProgram
from .models import ICProgramItem
from .models import ICSyncRequest
from .models import ICUpload


def ic_editor_required(view_func):
    """For IC views with no single object: dashboards, lists, creating programs."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_ic_editor:
            return forbidden("Only International Cinema editors can do that.")
        return view_func(request, *args, **kwargs)

    _wrapped.permission_check = "ic_editor"
    return _wrapped


ic_program_write_required = _object_permission_required(
    ICProgram, "program_id", "can_be_edited_by", "ic_program_write"
)
ic_item_preview_required = _object_permission_required(
    ICProgramItem, "item_id", "can_be_previewed_by", "ic_item_preview"
)


def ic_resource_upload_required(view_func):
    """Uploading a master needs the IC role and the same resource access as attaching files."""

    @wraps(view_func)
    def _wrapped(request, resource_id, *args, **kwargs):
        resource = get_object_or_404(Resource, pk=resource_id)
        if not (
            request.user.is_ic_editor and request.user.can_access_resource(resource)
        ):
            return forbidden(
                "Only International Cinema editors with access to this resource can do that."
            )
        return view_func(request, resource, *args, **kwargs)

    _wrapped.permission_check = "ic_resource_upload"
    return _wrapped


def ic_upload_owner_required(view_func):
    """Continuing an upload: the uploader (or a superuser), still with resource access."""

    @wraps(view_func)
    def _wrapped(request, upload_id, *args, **kwargs):
        upload = get_object_or_404(
            ICUpload.objects.select_related("resource"), pk=upload_id
        )
        user = request.user
        if not (
            user.is_ic_editor
            and user.can_access_resource(upload.resource)
            and (upload.owner_id == user.pk or user.is_superuser)
        ):
            return forbidden("This upload belongs to someone else.")
        return view_func(request, upload, *args, **kwargs)

    _wrapped.permission_check = "ic_upload_owner"
    return _wrapped


def ic_sync_request_read_required(view_func):
    @wraps(view_func)
    def _wrapped(request, sync_request_id, *args, **kwargs):
        sync_request = get_object_or_404(
            ICSyncRequest.objects.select_related("instance"), pk=sync_request_id
        )
        if not request.user.is_ic_editor:
            return forbidden("Only International Cinema editors can do that.")
        return view_func(request, sync_request, *args, **kwargs)

    _wrapped.permission_check = "ic_sync_request_read"
    return _wrapped
