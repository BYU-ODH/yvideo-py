from django.urls import path

from .views import add_annotation
from .views import create_collection
from .views import index
from .views import invalid_login
from .views import manage_collections
from .views import player
from .views import spoof_user_search
from .views import spoof_user_start
from .views import spoof_user_stop
from .views import stream_file

app_name = "core"

urlpatterns = [
    path("", index, name="index"),
    path("manage-collections/", manage_collections, name="manage_collections"),
    path("collections/create/", create_collection, name="create_collection"),
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
