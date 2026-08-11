"""Structural guards over the URL registry.

Issue #111's audit found 21 exploitable endpoints, nearly all of them views that simply
forgot an ownership check. A per-endpoint test cannot catch the 22nd, so these walk
core.urls and assert the properties that were missing, with an explicit exempt list as
the only way to opt out. Adding a view without a permission decorator fails here.
"""

from django.urls import get_resolver
from django.urls.resolvers import URLPattern
from django.urls.resolvers import URLResolver
import pytest

# Views that legitimately carry no object-level check, with the reason. Anything added
# here is a deliberate decision, not an oversight.
PERMISSION_EXEMPT_VIEWS = {
    # Public or identity-only.
    "index": "landing page; branches on the viewer's own privilege level",
    "invalid_login": "login failure page, deliberately unauthenticated",
    "playlists": "lists only what the requester can reach; filters rather than checks",
    "request_resource": "anyone may request a resource; owner is set from request.user",
    # Guarded by the spoofing decorators instead.
    "start_spoofing": "spoof_permission_required + can_spoof_as",
    "stop_spoofing": "spoof_permission_required",
    "spoof_user_search": "spoof_permission_required; excludes admins for lab assistants",
    # Checked inside the view against per-object state a decorator cannot resolve.
    "stream_file": "ResourceFileKey.can_be_used_by: binds the key to its user and expires it",
    "create_playlist": "instructor_required; there is no object yet",
    "display_annotation_set_create_option": "static form fragment, no object",
    "display_annotation_set_import_option": "static form fragment, no object",
    # Legacy migration, which checks capability and per-object ownership inline.
    "legacy_migration_requests": "filters to requests the user owns",
    "create_legacy_migration_request": "_can_request_migration; target_owner is request.user",
    "legacy_migration_request_detail": "checks requested_by / target_owner",
}

# Methods that change state. A view answering one of these without a method guard also
# answers GET, which is how delete_annotation_set was exploitable with a bare link.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Views that render on GET and write on POST -- the standard Django form pattern. The
# write still only happens on the POST branch, so CSRF protection covers it.
FORM_VIEWS = {"request_resource"}


def iter_url_patterns(resolver=None, prefix=""):
    if resolver is None:
        resolver = get_resolver()
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            yield from iter_url_patterns(pattern, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern


def core_url_patterns():
    """Only this project's own views; not admin, OIDC, or static."""
    for route, pattern in iter_url_patterns():
        module = getattr(pattern.callback, "__module__", "")
        if module.startswith("core.views"):
            yield route, pattern


def describe(pattern):
    return pattern.name or getattr(pattern.callback, "__name__", "<unnamed>")


ALL_HTTP_METHODS = {
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
}


def allowed_methods(view, seen=None):
    """The method list a django.views.decorators.http guard closed over, or None.

    Those decorators record nothing on the wrapper they return, so the list has to be
    recovered from the closure. Walks through our own decorators, which use @wraps, to
    find a guard applied at any depth.
    """
    if seen is None:
        seen = set()
    if id(view) in seen:
        return None
    seen.add(id(view))

    for cell in getattr(view, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:  # an empty cell in a recursive closure
            continue
        if isinstance(value, (list, tuple, set, frozenset)) and value:
            methods = {str(item).upper() for item in value}
            if methods <= ALL_HTTP_METHODS:
                return methods
        if callable(value):
            nested = allowed_methods(value, seen)
            if nested is not None:
                return nested
    return None


@pytest.mark.django_db
def test_every_view_declares_a_permission_check():
    unprotected = [
        f"{describe(pattern)} ({route})"
        for route, pattern in core_url_patterns()
        if not hasattr(pattern.callback, "permission_check")
        and describe(pattern) not in PERMISSION_EXEMPT_VIEWS
    ]
    assert not unprotected, (
        "These views carry no permission decorator from core.permissions. Wrap each in "
        "the appropriate *_required decorator, or add it to PERMISSION_EXEMPT_VIEWS "
        "with a reason:\n  " + "\n  ".join(sorted(unprotected))
    )


@pytest.mark.django_db
def test_every_mutating_view_has_a_method_guard():
    unguarded = []
    for route, pattern in core_url_patterns():
        allowed = allowed_methods(pattern.callback)
        if allowed is None:
            # Without a guard the view answers every method, including GET, so CSRF
            # protection never engages on the unsafe ones.
            unguarded.append(f"{describe(pattern)} ({route})")
            continue
        if (
            "GET" in allowed
            and UNSAFE_METHODS & allowed
            and describe(pattern) not in FORM_VIEWS
        ):
            unguarded.append(
                f"{describe(pattern)} ({route}) answers both GET and "
                f"{sorted(UNSAFE_METHODS & allowed)}"
            )
    assert not unguarded, (
        "These views need @require_GET, @require_POST, or @require_http_methods:\n  "
        + "\n  ".join(sorted(unguarded))
    )


@pytest.mark.django_db
def test_object_ids_are_not_taken_from_the_request_body():
    """A decorator cannot read a JSON body without consuming it, so ids live in the path.

    Checked by name rather than by inspecting bodies: the views whose ids used to come
    from json.loads(request.body) are exactly the ones whose URL now has to capture them.
    """
    expects_id_in_path = {
        "playlist_info": "playlist_id",
        "delete_playlist": "playlist_id",
        "render_course_assignment": "playlist_id",
        "assign_playlist_to_course": "playlist_id",
        "update_playlist_course_sections": "playlist_id",
        "unassign_playlist_from_course": "playlist_id",
        "display_playlist_settings": "playlist_id",
        "update_playlist_settings": "playlist_id",
        "create_content": "playlist_id",
        "create_content_from_youtube_url": "playlist_id",
        "update_content": "content_id",
        "delete_content": "content_id",
        "create_important_word": "content_id",
        "select_annotation_set": "content_id",
        "create_annotation_set": "content_id",
        "update_annotation_set_name": "annotation_set_id",
        "delete_annotation_set": "annotation_set_id",
        "export_annotation_set": "annotation_set_id",
        "update_track": "track_id",
        "create_track": "annotation_set_id",
        "update_tracks_stack_positions": "annotation_set_id",
        "delete_track": "track_id",
        "update_subtitle_content": "subtitle_id",
        "get_editable_subtitles": "subtitle_id",
    }
    routes = {pattern.name: route for route, pattern in core_url_patterns()}
    missing = [
        f"{name} expects <{id_kwarg}> in its path, got {routes.get(name)!r}"
        for name, id_kwarg in expects_id_in_path.items()
        if f"<int:{id_kwarg}>" not in (routes.get(name) or "")
    ]
    assert not missing, "\n  ".join(missing)
