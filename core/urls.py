from django.urls import path

from .views import create_collection
from .views import index
from .views import manage_collections
from .views import player
from .views import stream_file

app_name = "core"

urlpatterns = [
    path("", index, name="index"),
    path("manage-collections/", manage_collections, name="manage_collections"),
    path("collections/create/", create_collection, name="create_collection"),
    path("player/<int:content_id>", player, name="player"),
    path("stream/<int:file_key>", stream_file, name="stream_file"),
]
