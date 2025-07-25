from django.urls import path

from .views import index
from .views import player
from .views import stream_file

app_name = "core"

urlpatterns = [
    path("", index, name="index"),
    path("player/<int:content_id>", player, name="player"),
    path("stream/<int:file_key>", stream_file, name="stream_file"),
]
