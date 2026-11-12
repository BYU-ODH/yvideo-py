"""Production's /ic/ routes: the preview player, IC management, and the booth sync API."""

from django.urls import path

from . import views_manage
from . import views_player
from . import views_sync_api

urlpatterns = [
    # Preview player (the same pages the booth shows, playing the SD file).
    path("", views_player.control_index, name="ic_control_index"),
    path("program/<int:program_id>/", views_player.control, name="ic_control"),
    path("output/", views_player.output, name="ic_output"),
    path(
        "item/<int:item_id>/player-data/",
        views_player.item_player_data,
        name="ic_item_player_data",
    ),
    path("item/<int:item_id>/media/", views_player.item_media, name="ic_item_media"),
    # Management.
    path("manage/", views_manage.dashboard, name="ic_manage_dashboard"),
    path("manage/jobs/", views_manage.jobs_partial, name="ic_manage_jobs"),
    path("manage/programs/new/", views_manage.program_new, name="ic_program_new"),
    path(
        "manage/programs/create/", views_manage.program_create, name="ic_program_create"
    ),
    path(
        "manage/programs/<int:program_id>/",
        views_manage.program_edit,
        name="ic_program_edit",
    ),
    path(
        "manage/programs/<int:program_id>/update/",
        views_manage.program_update,
        name="ic_program_update",
    ),
    path(
        "manage/programs/<int:program_id>/delete/",
        views_manage.program_delete,
        name="ic_program_delete",
    ),
    path(
        "manage/programs/<int:program_id>/items/add/",
        views_manage.item_add,
        name="ic_item_add",
    ),
    path(
        "manage/programs/<int:program_id>/items/reorder/",
        views_manage.items_reorder,
        name="ic_items_reorder",
    ),
    path(
        "manage/programs/<int:program_id>/items/<int:item_id>/update/",
        views_manage.item_update,
        name="ic_item_update",
    ),
    path(
        "manage/programs/<int:program_id>/items/<int:item_id>/move/",
        views_manage.item_move,
        name="ic_item_move",
    ),
    path(
        "manage/programs/<int:program_id>/items/<int:item_id>/delete/",
        views_manage.item_delete,
        name="ic_item_delete",
    ),
    path("manage/resources/", views_manage.resources, name="ic_manage_resources"),
    path(
        "manage/resources/<int:resource_id>/use-sd/",
        views_manage.resource_use_sd,
        name="ic_resource_use_sd",
    ),
    path(
        "manage/resources/<int:resource_id>/projection-upload/",
        views_manage.projection_upload_page,
        name="ic_projection_upload",
    ),
    path(
        "manage/resources/<int:resource_id>/uploads/",
        views_manage.upload_create,
        name="ic_upload_create",
    ),
    path(
        "manage/uploads/<uuid:upload_id>/",
        views_manage.upload_status,
        name="ic_upload_status",
    ),
    path(
        "manage/uploads/<uuid:upload_id>/chunk/",
        views_manage.upload_chunk,
        name="ic_upload_chunk",
    ),
    path(
        "manage/uploads/<uuid:upload_id>/finalize/",
        views_manage.upload_finalize,
        name="ic_upload_finalize",
    ),
    path(
        "manage/instances/<int:instance_id>/push/",
        views_manage.instance_push,
        name="ic_instance_push",
    ),
    path(
        "manage/sync-requests/<int:sync_request_id>/",
        views_manage.sync_request_detail,
        name="ic_sync_request_detail",
    ),
    # Booth sync API (bearer token).
    path("sync/manifest/", views_sync_api.manifest, name="ic_sync_manifest"),
    path("sync/check/", views_sync_api.check, name="ic_sync_check"),
    path("sync/progress/", views_sync_api.progress, name="ic_sync_progress"),
    path("sync/fixture/", views_sync_api.fixture, name="ic_sync_fixture"),
    path(
        "sync/files/projection/<int:resource_file_id>/",
        views_sync_api.projection_file,
        name="ic_sync_projection_file",
    ),
    path(
        "sync/files/subtitle/<int:subtitle_id>/",
        views_sync_api.subtitle_file,
        name="ic_sync_subtitle_file",
    ),
    path("sync/status/", views_sync_api.status, name="ic_sync_status"),
]
