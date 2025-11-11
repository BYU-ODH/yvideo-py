from django.urls import path

from . import views

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
    path("clip-editor/<int:content_id>/", views.clip_editor, name="clip_editor"),
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
    path("clips/<int:clip_id>/edit/", views.load_clip_form, name="load_clip_form"),
    path("clips/<int:clip_id>/update/", views.update_clip, name="update_clip"),
    path("clips/<int:clip_id>/delete/", views.delete_clip, name="delete_clip"),
    path("clips/create/<int:content_id>/", views.create_clip, name="clip-create"),
]
