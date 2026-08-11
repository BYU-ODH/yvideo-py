from django.urls import path

from . import views
from . import views_legacy_migration
from . import views_video_editor

app_name = "core"

# Object ids belong in the path, not the request body: a permission decorator cannot
# read a JSON body without consuming it, and a body-supplied id is invisible to the
# routing layer. See core/permissions.py.
urlpatterns = [
    path("", views.index, name="index"),
    path(
        "legacy-migrations/",
        views_legacy_migration.legacy_migration_requests,
        name="legacy_migration_requests",
    ),
    path(
        "legacy-migrations/create/",
        views_legacy_migration.create_legacy_migration_request,
        name="create_legacy_migration_request",
    ),
    path(
        "legacy-migrations/<int:pk>/",
        views_legacy_migration.legacy_migration_request_detail,
        name="legacy_migration_request_detail",
    ),
    path("playlists/", views.playlists, name="playlists"),
    path("playlists/create/", views.create_playlist, name="create_playlist"),
    path(
        "playlists/<int:playlist_id>/",
        views.playlist_info,
        name="playlist_info",
    ),
    path(
        "playlists/<int:playlist_id>/delete/",
        views.delete_playlist,
        name="delete_playlist",
    ),
    path(
        "playlists/<int:playlist_id>/render-course-assignment/",
        views.render_course_assignment,
        name="render_course_assignment",
    ),
    path(
        "playlists/<int:playlist_id>/assign-course/",
        views.assign_playlist_to_course,
        name="assign_playlist_to_course",
    ),
    path(
        "playlists/<int:playlist_id>/course/update-sections/",
        views.update_playlist_course_sections,
        name="update_playlist_course_sections",
    ),
    path(
        "playlists/<int:playlist_id>/course/unassign/",
        views.unassign_playlist_from_course,
        name="unassign_playlist_from_course",
    ),
    path(
        "playlists/<int:playlist_id>/settings/",
        views.display_playlist_settings,
        name="display_playlist_settings",
    ),
    path(
        "playlists/<int:playlist_id>/settings/update/",
        views.update_playlist_settings,
        name="update_playlist_settings",
    ),
    path(
        "playlists/<int:playlist_id>/content/display-create/",
        views.display_create_content,
        name="display_create_content",
    ),
    path(
        "playlists/<int:playlist_id>/content/create/",
        views.create_content,
        name="create_content",
    ),
    path(
        "playlists/<int:playlist_id>/content/create-from-url/",
        views.create_content_from_youtube_url,
        name="create_content_from_youtube_url",
    ),
    path(
        "playlists/<int:playlist_id>/create-from-resource/",
        views.display_create_from_resource,
        name="display_create_from_resource",
    ),
    path(
        "playlists/<int:playlist_id>/create-from-resource/<int:resource_id>/form/",
        views.render_create_from_resource_form,
        name="render_create_from_resource_form",
    ),
    path(
        "resource-intake-request/",
        views.request_resource,
        name="request_resource",
    ),
    path(
        "content/<int:content_id>/display-settings/",
        views.display_content_info,
        name="display_content_info",
    ),
    path(
        "content/<int:content_id>/render-settings-form/",
        views.render_content_settings_form,
        name="render_content_settings_form",
    ),
    path(
        "content/<int:content_id>/update/", views.update_content, name="update_content"
    ),
    path(
        "content/<int:content_id>/delete/", views.delete_content, name="delete_content"
    ),
    path(
        "content/<int:content_id>/important-word/create/",
        views.create_important_word,
        name="create_important_word",
    ),
    path(
        "important-word/<int:word_id>/delete/",
        views.delete_important_word,
        name="delete_important_word",
    ),
    path(
        "content/<int:content_id>/player-data/",
        views.get_player_data,
        name="get_player_data",
    ),
    path("player/<int:content_id>/", views.player, name="player"),
    path("stream/<int:resource_file_key_id>/", views.stream_file, name="stream_file"),
    path("invalid-login", views.invalid_login, name="invalid_login"),
    path("spoof-user-start/", views.spoof_user_start, name="start_spoofing"),
    path("spoof-user-stop/", views.spoof_user_stop, name="stop_spoofing"),
    path("spoof-user-search/", views.spoof_user_search, name="spoof_user_search"),
    path(
        "subtitles/<int:subtitle_id>/editable/",
        views_video_editor.get_editable_subtitles,
        name="get_editable_subtitles",
    ),
    path(
        "subtitles/<int:subtitle_id>/update-cues/",
        views_video_editor.update_subtitle_content,
        name="update_subtitle_content",
    ),
    # Video editor page
    path(
        "video-editor/<int:content_id>/",
        views_video_editor.video_editor,
        name="video_editor",
    ),
    path(
        "video-editor/<int:content_id>/reload-player/",
        views_video_editor.get_player_wrapper_html,
        name="reload-video-player",
    ),
    # AnnotationSet management
    path(
        "content/<int:content_id>/select-annotation-set/",
        views_video_editor.select_annotation_set,
        name="select_annotation_set",
    ),
    path(
        "content/<int:content_id>/annotation-set/create/",
        views_video_editor.create_annotation_set,
        name="create_annotation_set",
    ),
    path(
        "annotation-set/<int:annotation_set_id>/settings/",
        views_video_editor.load_annotation_set_settings,
        name="load_annotation_set_settings",
    ),
    path(
        "annotation-set/<int:annotation_set_id>/update-name/",
        views_video_editor.update_annotation_set_name,
        name="update_annotation_set_name",
    ),
    path(
        "annotation-set/<int:annotation_set_id>/delete/",
        views_video_editor.delete_annotation_set,
        name="delete_annotation_set",
    ),
    path(
        "annotation-set/<int:annotation_set_id>/export/",
        views_video_editor.export_annotation_set,
        name="export_annotation_set",
    ),
    path(
        "annotation-set/<int:annotation_set_id>/panel/",
        views_video_editor.build_annotation_panel,
        name="get_annotation_panel",
    ),
    path(
        "annotation-options-modal/create",
        views_video_editor.display_annotation_set_create_option,
        name="display_annotation_set_create_option",
    ),
    path(
        "annotation-options-modal/import",
        views_video_editor.display_annotation_set_import_option,
        name="display_annotation_set_import_option",
    ),
    path(
        "annotation-options-modal/copy-from-set/<int:content_id>/",
        views_video_editor.display_copy_from_annotation_set_option,
        name="display_copy_from_annotation_set_option",
    ),
    path(
        "annotation-options-modal/use-existing/<int:content_id>/",
        views_video_editor.display_use_existing_annotation_set_option,
        name="display_use_existing_annotation_set_option",
    ),
    # Undo/Redo (per-annotation)
    path(
        "content/<int:content_id>/undo/",
        views_video_editor.undo_annotation,
        name="undo_annotation",
    ),
    path(
        "content/<int:content_id>/redo/",
        views_video_editor.redo_annotation,
        name="redo_annotation",
    ),
    # Track CRUD
    path(
        "track/<int:track_id>/update/",
        views_video_editor.update_track,
        name="update_track",
    ),
    path(
        "annotation-set/<int:annotation_set_id>/tracks/update_stack_positions/",
        views_video_editor.update_track_positions_in_set,
        name="update_tracks_stack_positions",
    ),
    path(
        "annotation-set/<int:annotation_set_id>/track/create/",
        views_video_editor.create_track,
        name="create_track",
    ),
    path(
        "track/<int:track_id>/delete/",
        views_video_editor.delete_track,
        name="delete_track",
    ),
    # Annotation CRUD
    path(
        "track/<int:track_id>/annotations/<str:annotation_type>/create/",
        views_video_editor.create_annotation,
        name="create_annotation",
    ),
    path(
        "content/<int:content_id>/annotations/<str:annotation_type>/<int:annotation_id>/update/",
        views_video_editor.update_annotation,
        name="update_annotation",
    ),
    path(
        "annotations/<str:annotation_type>/<int:annotation_id>/delete",
        views_video_editor.delete_annotation,
        name="delete_annotation",
    ),
    path(
        "annotations/blur/<int:annotation_id>/positions/",
        views_video_editor.upsert_blur_position,
        name="upsert_blur_position",
    ),
    path(
        "annotations/blur/positions/<int:position_id>/",
        views_video_editor.delete_blur_position,
        name="delete_blur_position",
    ),
    path(
        "annotations/<str:annotation_type>/<int:annotation_id>/form/",
        views_video_editor.load_annotation_form,
        name="load_annotation_form",
    ),
]
