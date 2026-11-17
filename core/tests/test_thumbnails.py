import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test import modify_settings
from django.urls import reverse

from core.factories import ContentFactory
from core.factories import PlaylistFactory
from core.factories import PlaylistUserAccessFactory
from core.factories import UserFactory
from core.models import Content
from core.models import Thumbnail
from core.youtube_utils import get_or_create_youtube_resource

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


def thumbnail_form(**overrides):
    return {
        "background_color": "#123456",
        "text": "Week 1",
        "text_color": "#ffffff",
        "text_size": "2",
        "image_source": "keep",
        **overrides,
    }


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
class ThumbnailTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner, published=True)
        self.client.force_login(self.owner)

    def save_playlist_thumbnail(self, **overrides):
        return self.client.post(
            reverse("save_playlist_thumbnail", args=[self.playlist.pk]),
            thumbnail_form(**overrides),
        )

    def test_creating_a_playlist_points_to_its_thumbnail_editor(self):
        response = self.client.post(
            reverse("create_playlist"),
            json.dumps({"name": "New playlist"}),
            content_type="application/json",
        )

        editor_url = response.json()["thumbnail_editor_url"]
        editor = self.client.get(editor_url)
        self.assertContains(editor, 'value="New playlist"')

    def test_creating_content_points_to_its_thumbnail_editor(self):
        source = ContentFactory(playlist=self.playlist)
        response = self.client.post(
            reverse("create_content", args=[self.playlist.pk]),
            json.dumps({"title": "Clip", "resource_file_id": source.resource_file_id}),
            content_type="application/json",
        )

        content = Content.objects.get(title="Clip")
        editor = self.client.get(response.json()["thumbnail_editor_url"])
        self.assertContains(editor, f'value="{content.resource.name}"')
        self.assertContains(editor, 'id="thumbnail-video"')

    def test_items_without_a_saved_thumbnail_show_defaults(self):
        content = ContentFactory(playlist=self.playlist)

        response = self.client.get(reverse("playlist_info", args=[self.playlist.pk]))

        self.assertContains(response, f"{content.resource.name}</span>")

    def test_uploaded_image_is_shown_to_viewers(self):
        self.save_playlist_thumbnail(
            image_source="upload",
            image=SimpleUploadedFile("t.jpg", JPEG, content_type="image/jpeg"),
        )
        listing = self.client.get(reverse("playlists"))
        self.assertContains(listing, "Week 1")
        self.assertContains(
            listing, reverse("playlist_thumbnail_image", args=[self.playlist.pk])
        )

        student = UserFactory()
        PlaylistUserAccessFactory(user=student, playlist=self.playlist)
        self.client.force_login(student)
        image = self.client.get(
            reverse("playlist_thumbnail_image", args=[self.playlist.pk])
        )
        self.assertEqual(image.content, JPEG)
        self.assertEqual(image["Content-Type"], "image/jpeg")

    def test_non_jpeg_upload_is_rejected(self):
        response = self.save_playlist_thumbnail(
            image_source="upload",
            image=SimpleUploadedFile("t.html", b"<script>", content_type="image/jpeg"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Thumbnail.objects.exists())

    def test_unchanged_default_text_follows_later_renames(self):
        self.save_playlist_thumbnail(text=self.playlist.name)
        self.playlist.name = "Renamed"
        self.playlist.save()

        response = self.client.get(reverse("playlists"))

        self.assertContains(response, "Renamed</span>")

    def test_custom_text_survives_renames(self):
        self.save_playlist_thumbnail(text="Week 1")
        self.playlist.name = "Renamed"
        self.playlist.save()

        response = self.client.get(reverse("playlists"))

        self.assertContains(response, "Week 1</span>")

    def test_invalid_color_is_rejected(self):
        response = self.save_playlist_thumbnail(background_color="red;}")

        self.assertEqual(response.status_code, 400)

    def test_switching_to_color_only_removes_the_image(self):
        self.save_playlist_thumbnail(
            image_source="upload",
            image=SimpleUploadedFile("t.jpg", JPEG, content_type="image/jpeg"),
        )
        self.save_playlist_thumbnail(image_source="none")

        response = self.client.get(
            reverse("playlist_thumbnail_image", args=[self.playlist.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_youtube_content_can_use_the_youtube_thumbnail(self):
        content = ContentFactory(
            playlist=self.playlist,
            resource_file=None,
            resource=get_or_create_youtube_resource("abcdefghijk", "owner"),
            url="https://www.youtube.com/watch?v=abcdefghijk",
        )
        with mock.patch("core.views_thumbnails.requests.get") as get:
            get.return_value.content = JPEG
            response = self.client.post(
                reverse("save_content_thumbnail", args=[content.pk]),
                thumbnail_form(image_source="youtube"),
            )

        self.assertEqual(response.status_code, 204)
        self.assertIn("abcdefghijk", get.call_args.args[0])
        self.assertEqual(bytes(content.thumbnail.image), JPEG)

    def test_strangers_cannot_edit_thumbnails(self):
        stranger = UserFactory()
        self.client.force_login(stranger)

        editor = self.client.get(
            reverse("playlist_thumbnail_editor", args=[self.playlist.pk])
        )
        save = self.save_playlist_thumbnail()

        self.assertEqual(editor.status_code, 403)
        self.assertEqual(save.status_code, 403)
