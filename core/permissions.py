"""Object-level permission decorators for the view layer.

Every decorator here resolves the object named in the URL, checks one predicate on
it, and hands the object to the view in place of its id. Views therefore cannot
reach an object they were not authorized for, which is what makes the checks
structural rather than remembered. test_view_permissions.py walks the URL registry
and asserts every view carries one of these or is on an explicit exempt list.

Ids belong in the URL rather than the request body for the same reason: a decorator
cannot read a JSON body without consuming it.
"""

from functools import wraps

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404

from .models import AnnotationSet
from .models import Content
from .models import ImportantWord
from .models import Playlist
from .models import Subtitle
from .models import Track

# 403 for an object that exists but is not yours, everywhere. A 404 would leak less
# but makes every genuine bug indistinguishable from a permission problem, and the
# ids are sequential and enumerable anyway (#320).
FORBIDDEN_MESSAGE = "You do not have permission to do that."


def forbidden(message=FORBIDDEN_MESSAGE):
    return HttpResponseForbidden(message)


def _object_permission_required(model, id_kwarg, predicate_name, check_name):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if id_kwarg not in kwargs:
                # Never a request problem: the route did not capture the id this
                # decorator needs, so it would otherwise look up pk=None and 404.
                raise ImproperlyConfigured(
                    f"{view_func.__name__} is wrapped in a decorator expecting "
                    f"<int:{id_kwarg}> in its URL, but the route did not capture it."
                )
            instance = get_object_or_404(model, pk=kwargs.pop(id_kwarg))
            if not getattr(instance, predicate_name)(request.user):
                return forbidden()
            return view_func(request, instance, *args, **kwargs)

        _wrapped.permission_check = check_name
        return _wrapped

    return decorator


playlist_admin_required = _object_permission_required(
    Playlist, "playlist_id", "can_be_administered_by", "playlist_admin"
)
playlist_write_required = _object_permission_required(
    Playlist, "playlist_id", "can_be_edited_by", "playlist_write"
)
playlist_read_required = _object_permission_required(
    Playlist, "playlist_id", "can_be_viewed_by", "playlist_read"
)
content_write_required = _object_permission_required(
    Content, "content_id", "can_be_edited_by", "content_write"
)
content_read_required = _object_permission_required(
    Content, "content_id", "can_be_viewed_by", "content_read"
)
annotation_set_write_required = _object_permission_required(
    AnnotationSet, "annotation_set_id", "can_be_edited_by", "annotation_set_write"
)
annotation_set_read_required = _object_permission_required(
    AnnotationSet, "annotation_set_id", "can_be_read_by", "annotation_set_read"
)
subtitle_write_required = _object_permission_required(
    Subtitle, "subtitle_id", "can_be_edited_by", "subtitle_write"
)
subtitle_read_required = _object_permission_required(
    Subtitle, "subtitle_id", "can_be_read_by", "subtitle_read"
)


def track_write_required(view_func):
    """A track is editable exactly when its annotation set is."""

    @wraps(view_func)
    def _wrapped(request, track_id, *args, **kwargs):
        track = get_object_or_404(Track, pk=track_id)
        if not track.annotation_set.can_be_edited_by(request.user):
            return forbidden()
        return view_func(request, track, *args, **kwargs)

    _wrapped.permission_check = "track_write"
    return _wrapped


def important_word_write_required(view_func):
    """An important word is editable exactly when its content is."""

    @wraps(view_func)
    def _wrapped(request, word_id, *args, **kwargs):
        word = get_object_or_404(ImportantWord, pk=word_id)
        if not word.content.can_be_edited_by(request.user):
            return forbidden()
        return view_func(request, word, *args, **kwargs)

    _wrapped.permission_check = "important_word_write"
    return _wrapped


def checks_permission_inline(reason):
    """Mark a view whose object cannot be resolved by a decorator.

    The annotation views look their object up through ANNOTATION_MODELS by a type
    string in the URL, so there is no single model for a decorator to fetch. They
    check the owning annotation set themselves; this records that for the registry
    test rather than letting them look unprotected.
    """

    def decorator(view_func):
        view_func.permission_check = f"inline: {reason}"
        return view_func

    return decorator


def instructor_required(view_func):
    """For views that create objects, where there is no object to check yet."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_instructor:
            return forbidden("Only instructors can do that.")
        return view_func(request, *args, **kwargs)

    _wrapped.permission_check = "instructor"
    return _wrapped
