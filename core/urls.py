from django.urls import path

from . import views
from . import views_clip_editor
from . import views_video_editor

app_name = "core"

urlpatterns = [
    path("", views.index, name="index"),
    path("manage-collections/", views.manage_collections, name="manage_collections"),
    path("collections/create/", views.create_collection, name="create_collection"),
    path("collections/view/<int:pk>/", views.view_collection, name="view_collection"),
    path(
        "display-collection-contents/<int:collection_id>/",
        views.display_collection_contents,
        name="display_collection_contents",
    ),
    path(
        "collection/display-settings/<int:collection_id>/",
        views.display_collection_settings,
        name="display_collection_settings",
    ),
    path(
        "collection/update",
        views.update_collection_settings,
        name="update_collection_settings",
    ),
    path(
        "collection/delete/<int:collection_id>",
        views.delete_collection,
        name="delete_collection",
    ),
    path(
        "content/display-create/<int:collection_id>/",
        views.display_create_content,
        name="display_create_content",
    ),
    path("content/create", views.create_content, name="create_content"),
    path(
        "display-resources-files/",
        views.display_resources_files,
        name="display_resources_files",
    ),
    path(
        "content/display-settings/<int:content_id>/",
        views.display_content_settings,
        name="display_content_settings",
    ),
    path("content/update", views.update_content, name="update_content"),
    path(
        "content/delete/<int:content_id>", views.delete_content, name="delete_content"
    ),
    path(
        "important-word/create",
        views.create_important_word,
        name="create_important_word",
    ),
    path(
        "important-word/delete/<int:word_id>/",
        views.delete_important_word,
        name="delete_important_word",
    ),
    path("player/<int:content_id>/", views.player, name="player"),
    path(
        "clip-editor/<int:content_id>/",
        views_clip_editor.clip_editor,
        name="clip_editor",
    ),
    path("stream/<int:file_key>/", views.stream_file, name="stream_file"),
    path("player/<int:content_id>", views.player, name="player"),
    path("stream/<int:file_key>", views.stream_file, name="stream_file"),
    path(
        "add_annotation/<str:annotation_type>/<int:file_id>/",
        views.add_annotation,
        name="add_annotation",
    ),
    path("invalid-login", views.invalid_login, name="invalid_login"),
    path("spoof-user-start/", views.spoof_user_start, name="start_spoofing"),
    path("spoof-user-stop/", views.spoof_user_stop, name="stop_spoofing"),
    path("spoof-user-search/", views.spoof_user_search, name="spoof_user_search"),
    path(
        "subtitle-editor/<int:content_id>/",
        views.subtitle_editor,
        name="subtitle_editor",
    ),
    path(
        "subtitle-editor/update-temp-file",
        views.update_subtitle_temp_file,
        name="update_subtitle_temp_file",
    ),
    path(
        "clips/<str:item_type>/<int:clip_id>/edit/",
        views_clip_editor.load_clip_form,
        name="load_clip_form",
    ),
    path(
        "clips/<str:item_type>/<int:clip_id>/update/",
        views_clip_editor.update_clip,
        name="update_clip",
    ),
    path(
        "clips/<str:item_type>/<int:clip_id>/delete/",
        views_clip_editor.delete_clip,
        name="delete_clip",
    ),
    path(
        "clips/<str:annotation_type>/create/content/<int:content_id>/",
        views_clip_editor.create_clip,
        name="create_clip",
    ),
    # Video editor page
    path(
        "content/<int:content_id>/video-editor/",
        views_video_editor.video_editor,
        name="video_editor",
    ),
    # AnnotationSet management
    path(
        "content/<int:content_id>/select-annotation-set/",
        views_video_editor.select_annotation_set,
        name="select_annotation_set",
    ),
    path(
        "annotation-set/<int:annotation_set_id>/add-editor/",
        views_video_editor.add_editor_to_annotation_set,
        name="add_editor_to_annotation_set",
    ),
    path(
        "annotation-set/<int:annotation_set_id>/remove-editor/<int:user_id>/",
        views_video_editor.remove_editor_from_annotation_set,
        name="remove_editor_from_annotation_set",
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
    # Annotation CRUD
    path(
        "annotations/<str:annotation_type>/create/content/<int:content_id>/",
        views_video_editor.create_annotation,
        name="create_annotation",
    ),
    path(
        "annotations/<str:annotation_type>/<int:annotation_id>/update/",
        views_video_editor.update_annotation,
        name="update_annotation",
    ),
    path(
        "annotations/<str:annotation_type>/<int:annotation_id>/delete/",
        views_video_editor.delete_annotation,
        name="delete_annotation",
    ),
    path(
        "annotations/<str:annotation_type>/<int:annotation_id>/form/",
        views_video_editor.load_annotation_form,
        name="load_annotation_form",
    ),
]
