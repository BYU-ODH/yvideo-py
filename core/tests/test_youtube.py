import json

from django.test import TestCase
from django.test import modify_settings
from django.urls import reverse

from core.factories import ContentFactory
from core.factories import PlaylistFactory
from core.factories import UserFactory
from core.models import Content
from core.models import Resource
from core.youtube import get_or_create_youtube_resource
from core.youtube import parse_youtube_video_id


class ParseYoutubeVideoIdTests(TestCase):
    def test_watch_url_with_extra_params(self):
        self.assertEqual(
            parse_youtube_video_id("https://www.youtube.com/watch?v=eHEsJyVQn3w&t=27s"),
            "eHEsJyVQn3w",
        )

    def test_youtu_be_url_with_share_tracking_param(self):
        self.assertEqual(
            parse_youtube_video_id("https://youtu.be/3W8pr0tiijs?si=abc123"),
            "3W8pr0tiijs",
        )

    def test_shorts_url(self):
        self.assertEqual(
            parse_youtube_video_id("https://www.youtube.com/shorts/abcdefghijk"),
            "abcdefghijk",
        )

    def test_embed_url(self):
        self.assertEqual(
            parse_youtube_video_id("https://www.youtube.com/embed/abcdefghijk"),
            "abcdefghijk",
        )

    def test_mobile_subdomain(self):
        self.assertEqual(
            parse_youtube_video_id("https://m.youtube.com/watch?v=abcdefghijk"),
            "abcdefghijk",
        )

    def test_unrecognized_url_returns_none(self):
        self.assertIsNone(parse_youtube_video_id("https://vimeo.com/12345"))

    def test_unrecognized_path_on_youtube_domain_returns_none(self):
        self.assertIsNone(parse_youtube_video_id("https://www.youtube.com/"))

    def test_empty_or_missing_url_returns_none(self):
        self.assertIsNone(parse_youtube_video_id(""))
        self.assertIsNone(parse_youtube_video_id(None))


class GetOrCreateYoutubeResourceTests(TestCase):
    def test_creates_resource_with_correct_fields(self):
        resource = get_or_create_youtube_resource("abcdefghijk", "123456789")

        self.assertEqual(resource.name, "YouTube: abcdefghijk")
        self.assertEqual(resource.media_type, Resource.MediaType.WEB)
        self.assertEqual(resource.requester_username, "123456789")
        self.assertEqual(resource.imdb_id, "YTabcdefghijk")

    def test_same_video_id_dedupes_to_one_resource(self):
        first = get_or_create_youtube_resource("abcdefghijk", "123456789")
        second = get_or_create_youtube_resource("abcdefghijk", "987654321")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            Resource.objects.filter(name="YouTube: abcdefghijk").count(), 1
        )

    def test_different_video_ids_get_different_resources(self):
        first = get_or_create_youtube_resource("abcdefghijk", "123456789")
        second = get_or_create_youtube_resource("zyxwvutsrqp", "123456789")

        self.assertNotEqual(first.pk, second.pk)


class CreateContentFromUrlViewTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner)

    def _post(self, **overrides):
        data = {
            "playlist_id": self.playlist.pk,
            "title": "A YouTube Video",
            "url": "https://www.youtube.com/watch?v=eHEsJyVQn3w",
        }
        data.update(overrides)
        return self.client.post(
            reverse("create_content_from_url"),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_requires_login(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)

    def test_owner_can_create_youtube_content(self):
        self.client.force_login(self.owner)
        response = self._post()

        self.assertEqual(response.status_code, 200)
        content = Content.objects.get(playlist=self.playlist, title="A YouTube Video")
        self.assertTrue(content.is_url_only())
        self.assertEqual(content.url, "https://www.youtube.com/watch?v=eHEsJyVQn3w")
        self.assertEqual(content.get_resource().name, "YouTube: eHEsJyVQn3w")

    def test_non_owner_non_admin_is_rejected(self):
        other_user = UserFactory()
        self.client.force_login(other_user)
        response = self._post()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Content.objects.filter(playlist=self.playlist).exists())

    def test_admin_can_create_for_other_users_playlist(self):
        admin = UserFactory(admin=True)
        self.client.force_login(admin)
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Content.objects.filter(playlist=self.playlist).exists())

    def test_unrecognized_url_is_rejected(self):
        self.client.force_login(self.owner)
        response = self._post(url="https://vimeo.com/12345")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Content.objects.filter(playlist=self.playlist).exists())

    def test_same_video_reused_across_playlists_shares_one_resource(self):
        self.client.force_login(self.owner)
        other_playlist = PlaylistFactory(owner=self.owner)

        self._post(title="First")
        self._post(playlist_id=other_playlist.pk, title="Second")

        resources = Resource.objects.filter(name="YouTube: eHEsJyVQn3w")
        self.assertEqual(resources.count(), 1)
        first_content = Content.objects.get(playlist=self.playlist, title="First")
        second_content = Content.objects.get(playlist=other_playlist, title="Second")
        self.assertEqual(first_content.get_resource(), second_content.get_resource())


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
class PlayerViewYoutubeRenderingTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)

    def test_youtube_content_renders_youtube_video_element(self):
        playlist = PlaylistFactory(owner=self.owner)
        resource = get_or_create_youtube_resource("eHEsJyVQn3w", self.owner.username)
        content = Content.objects.create(
            playlist=playlist,
            title="YouTube Content",
            url="https://www.youtube.com/watch?v=eHEsJyVQn3w",
            resource=resource,
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse("player", args=[content.pk]))

        self.assertContains(response, "<youtube-video")
        self.assertContains(response, 'data-video-id="eHEsJyVQn3w"')
        self.assertNotContains(response, "<video")

    def test_file_backed_content_still_renders_video_tag(self):
        content = ContentFactory(playlist__owner=self.owner)

        self.client.force_login(self.owner)
        response = self.client.get(reverse("player", args=[content.pk]))

        self.assertContains(response, "<video")
        self.assertNotContains(response, "<youtube-video")
