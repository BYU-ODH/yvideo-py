from django.urls import path

from .views import create_collection
from .views import create_important_word
from .views import delete_important_word
from .views import display_collection_contents
from .views import display_content_settings
from .views import index
from .views import manage_collections
from .views import player
from .views import stream_file
from .views import update_content
from .views import view_collection

app_name = "core"

urlpatterns = [
    path("", index, name="index"),
    path("manage-collections/", manage_collections, name="manage_collections"),
    path("collections/create/", create_collection, name="create_collection"),
    path("collections/view/<int:pk>/", view_collection, name="view_collection"),
    path(
        "display-collection-contents/<int:collection_id>",
        display_collection_contents,
        name="display_collection_contents",
    ),
    path(
        "content/display-settings/<int:content_id>/",
        display_content_settings,
        name="display_content_settings",
    ),
    path("content/update", update_content, name="update_content"),
    path("important-word/create", create_important_word, name="create_important_word"),
    path(
        "important-word/delete/<int:word_id>/",
        delete_important_word,
        name="delete_important_word",
    ),
    path("player/<int:content_id>/", player, name="player"),
    path("stream/<int:file_key>/", stream_file, name="stream_file"),
]
