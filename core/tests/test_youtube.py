import json

from django.test import TestCase
from django.test import modify_settings
from django.urls import reverse

from core.factories import ContentFactory
from core.factories import PlaylistFactory
from core.factories import ResourceFactory
from core.factories import UserFactory
from core.models import Content
from core.models import Resource
from core.youtube_utils import get_or_create_youtube_resource
from core.youtube_utils import parse_youtube_video_id
from core.youtube_utils import youtube_video_id_for_content
from core.youtube_utils import youtube_video_id_for_resource


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

    def test_shorts_url_is_rejected(self):
        # Not an oversight: a Short is vertical, and the player reports a constant 16:9 as its
        # intrinsic size, so contentRect() would place the annotation overlay on a rectangle
        # roughly three times wider than the picture. Blurs would be stored and drawn against
        # a frame the video does not occupy. See parse_youtube_video_id's docstring.
        self.assertIsNone(
            parse_youtube_video_id("https://www.youtube.com/shorts/abcdefghijk")
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


class YoutubeVideoIdForContentTests(TestCase):
    """Which video an embed shows, when the Content's URL and its Resource disagree.

    They can: only create_content_from_youtube_url ties the two together, and nothing stops a Content's
    URL being edited afterwards - which keeps the original Resource, and with it the annotation
    sets authored against the original video.
    """

    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner)

    def _content(self, url, resource):
        return Content.objects.create(
            playlist=self.playlist, title="Content", url=url, resource=resource
        )

    def test_resource_wins_when_the_url_points_somewhere_else(self):
        # The annotations belong to the Resource's sets, so the Resource names the video they
        # were actually authored against.
        resource = get_or_create_youtube_resource("eHEsJyVQn3w", self.owner.username)
        content = self._content("https://www.youtube.com/watch?v=abcdefghijk", resource)

        self.assertEqual(
            youtube_video_id_for_content(content, content.url), "eHEsJyVQn3w"
        )

    def test_url_is_used_when_the_resource_is_not_youtube_backed(self):
        # Covers Content created by any path that did not go through
        # get_or_create_youtube_resource.
        resource = ResourceFactory()
        content = self._content("https://www.youtube.com/watch?v=abcdefghijk", resource)

        self.assertEqual(
            youtube_video_id_for_content(content, content.url), "abcdefghijk"
        )

    def test_a_missing_source_url_yields_nothing(self):
        # The load-bearing one: a falsy source_url means get_content_source_url refused this
        # user, and the Resource is readable without any such check - so reading the id off it
        # first would hand the video to someone not allowed to view the content.
        resource = get_or_create_youtube_resource("eHEsJyVQn3w", self.owner.username)
        content = self._content("https://www.youtube.com/watch?v=eHEsJyVQn3w", resource)

        self.assertIsNone(youtube_video_id_for_content(content, None))

    def test_non_youtube_content_yields_nothing(self):
        resource = ResourceFactory()
        content = self._content("https://vimeo.com/12345", resource)

        self.assertIsNone(youtube_video_id_for_content(content, content.url))

    def test_a_resource_id_that_is_not_a_video_id_is_ignored(self):
        # "YT" is only a prefix by convention; a BYU/IMDb id must not be mistaken for one.
        resource = ResourceFactory(imdb_id="BYU0000000001")

        self.assertIsNone(youtube_video_id_for_resource(resource))

    def test_a_resource_with_no_id_is_ignored(self):
        resource = ResourceFactory()
        Resource.objects.filter(pk=resource.pk).update(imdb_id=None)

        self.assertIsNone(
            youtube_video_id_for_resource(Resource.objects.get(pk=resource.pk))
        )


class CreateContentFromUrlViewTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner)

    def _post(self, **overrides):
        data = {
            "title": "A YouTube Video",
            "url": "https://www.youtube.com/watch?v=eHEsJyVQn3w",
        }
        playlist_id = overrides.pop("playlist_id", self.playlist.pk)
        data.update(overrides)
        return self.client.post(
            reverse("create_content_from_youtube_url", args=[playlist_id]),
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

    def test_a_shorts_url_is_rejected_with_an_explanation(self):
        self.client.force_login(self.owner)
        response = self._post(url="https://www.youtube.com/shorts/abcdefghijk")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Shorts", response.content.decode())
        self.assertFalse(Content.objects.filter(playlist=self.playlist).exists())

    def test_a_malformed_body_is_a_bad_request_not_a_server_error(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("create_content_from_youtube_url", args=[self.playlist.pk]),
            data="not json",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_a_name_collision_is_reported_rather_than_a_bare_500(self):
        # Resource.name is unique and get_or_create keys on imdb_id, so a Resource already
        # holding this video's generated name under a different id makes the create fail. Only
        # an admin can fix that, so the message has to say so instead of becoming a 500.
        self.client.force_login(self.owner)
        ResourceFactory(name="YouTube: eHEsJyVQn3w", imdb_id="tt1234567")

        response = self._post()

        self.assertEqual(response.status_code, 409)
        self.assertIn("administrator", response.content.decode())
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


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
class DisplayContentInfoYoutubeRenderingTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)

    def test_youtube_content_renders_without_error(self):
        playlist = PlaylistFactory(owner=self.owner)
        resource = get_or_create_youtube_resource("eHEsJyVQn3w", self.owner.username)
        content = Content.objects.create(
            playlist=playlist,
            title="YouTube Content",
            url="https://www.youtube.com/watch?v=eHEsJyVQn3w",
            resource=resource,
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse("display_content_info", args=[content.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<youtube-video")
        self.assertContains(response, 'data-video-id="eHEsJyVQn3w"')
