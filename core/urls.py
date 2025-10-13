from django.urls import path

from .views import add_annotation
from .views import create_collection
from .views import create_content
from .views import create_important_word
from .views import delete_collection
from .views import delete_content
from .views import delete_important_word
from .views import display_collection_contents
from .views import display_collection_settings
from .views import display_content_settings
from .views import display_create_content
from .views import index
from .views import invalid_login
from .views import manage_collections
from .views import player
from .views import spoof_user_search
from .views import spoof_user_start
from .views import spoof_user_stop
from .views import stream_file
from .views import update_collection_settings
from .views import update_content
from .views import view_collection

app_name = "core"

urlpatterns = [
    path("", index, name="index"),
    path("manage-collections/", manage_collections, name="manage_collections"),
    path("collections/create/", create_collection, name="create_collection"),
    path("collections/view/<int:pk>/", view_collection, name="view_collection"),
    path(
        "display-collection-contents/<int:collection_id>/",
        display_collection_contents,
        name="display_collection_contents",
    ),
    path(
        "collection/display-settings/<int:collection_id>/",
        display_collection_settings,
        name="display_collection_settings",
    ),
    path(
        "collection/update",
        update_collection_settings,
        name="update_collection_settings",
    ),
    path(
        "collection/delete/<int:collection_id>",
        delete_collection,
        name="delete_collection",
    ),
    path(
        "content/display-create/<int:collection_id>/",
        display_create_content,
        name="display_create_content",
    ),
    path("content/create", create_content, name="create_content"),
    path(
        "content/display-settings/<int:content_id>/",
        display_content_settings,
        name="display_content_settings",
    ),
    path("content/update", update_content, name="update_content"),
    path("content/delete/<int:content_id>", delete_content, name="delete_content"),
    path("important-word/create", create_important_word, name="create_important_word"),
    path(
        "important-word/delete/<int:word_id>/",
        delete_important_word,
        name="delete_important_word",
    ),
    path("player/<int:content_id>/", player, name="player"),
    path("stream/<int:file_key>/", stream_file, name="stream_file"),
    path("player/<int:content_id>", player, name="player"),
    path("stream/<int:file_key>", stream_file, name="stream_file"),
    path(
        "add_annotation/<str:annotation_type>/<int:file_id>/",
        add_annotation,
        name="add_annotation",
    ),
    path("invalid-login", invalid_login, name="invalid_login"),
    path("spoof-user-start/", spoof_user_start, name="start_spoofing"),
    path("spoof-user-stop/", spoof_user_stop, name="stop_spoofing"),
    path("spoof-user-search/", spoof_user_search, name="spoof_user_search"),
]
