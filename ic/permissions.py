"""Permission decorators for International Cinema views, in core.permissions' style.

Each checks one predicate and records `permission_check` for the URL-registry test.
Programs need only the IC role; uploads also need access to the resource.
"""

from functools import wraps

from django.shortcuts import get_object_or_404

from core.models import Resource
from core.permissions import forbidden

from .models import ICUpload


def ic_editor_required(view_func):
    """Every IC editor may manage every program, so IC views need only the role."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_ic_editor:
            return forbidden("Only International Cinema editors can do that.")
        return view_func(request, *args, **kwargs)

    _wrapped.permission_check = "ic_editor"
    return _wrapped


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
