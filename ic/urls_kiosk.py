"""The booth kiosk's /ic/ routes (IS_IC_INSTANCE, bound to localhost)."""

from django.urls import path

from . import views_booth

urlpatterns = [
    path("", views_booth.control_index, name="ic_control_index"),
    path("program/<int:program_id>/", views_booth.control, name="ic_control"),
    path("output/", views_booth.output, name="ic_output"),
    path(
        "item/<int:item_id>/player-data/",
        views_booth.item_player_data,
        name="ic_item_player_data",
    ),
    path(
        "file/<int:resource_file_id>/<str:xxh64>/",
        views_booth.local_file,
        name="ic_local_file",
    ),
    path("heartbeat/", views_booth.heartbeat, name="ic_heartbeat"),
    path("status/", views_booth.status, name="ic_booth_status"),
    path("sync/badge/", views_booth.sync_badge, name="ic_sync_badge"),
    path("sync/run/", views_booth.sync_run_create, name="ic_sync_run_create"),
    path(
        "sync/run/<uuid:run_id>/",
        views_booth.sync_run_detail,
        name="ic_sync_run_detail",
    ),
]
