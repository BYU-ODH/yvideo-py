"""The booth: userless kiosk, read-only middleware, and file-addressed media."""

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.test import override_settings
from django.urls import NoReverseMatch
from django.urls import reverse
from django.utils import timezone

from core.factories import ResourceFactory
from ic import booth_state
from ic.models import ICLocalFile
from ic.models import ICSyncRun
from ic.testing import ICTempDirsMixin
from ic.testing import make_master
from ic.testing import make_program_scenario

BOOTH_MIDDLEWARE = [
    m
    for m in settings.MIDDLEWARE
    if m
    not in (
        "django.contrib.auth.middleware.LoginRequiredMiddleware",
        "core.middleware.SpoofUserMiddleware",
        "mozilla_django_oidc.middleware.SessionRefresh",
    )
] + ["ic.middleware.ICReadOnlyMiddleware"]


def mark_local(item):
    master = item.projection_file()
    return ICLocalFile.objects.create(
        kind=ICLocalFile.Kind.PROJECTION,
        ref_id=master.pk,
        name=master.file.name,
        size=master.size_bytes,
        xxh64=master.checksum,
        verified_at=timezone.now(),
    )


@override_settings(
    IS_IC_INSTANCE=True, ROOT_URLCONF="yvideo.urls_ic", MIDDLEWARE=BOOTH_MIDDLEWARE
)
class KioskTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.film = self.scenario["film_item"]
        self.program = self.scenario["program"]

    def test_pages_work_without_any_user(self):
        mark_local(self.film)
        for url in (
            reverse("ic_control_index"),
            reverse("ic_control", args=[self.program.pk]),
            reverse("ic_output"),
            reverse("ic_booth_status"),
            reverse("ic_sync_badge"),
            reverse("ic_item_player_data", args=[self.film.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertRedirects(self.client.get("/"), reverse("ic_control_index"))

    def test_only_booth_routes_exist(self):
        for name in (
            "video_editor",
            "admin:index",
            "playlists",
            "stream_file",
            "ic_manage_dashboard",
            "ic_sync_manifest",
            "ic_item_media",
        ):
            with self.subTest(name=name), self.assertRaises(NoReverseMatch):
                reverse(name, args=[1]) if name in (
                    "video_editor",
                    "stream_file",
                ) else reverse(name)

    def test_media_is_the_verified_local_master_addressed_by_checksum(self):
        player_data = reverse("ic_item_player_data", args=[self.film.pk])
        self.assertEqual(self.client.get(player_data).status_code, 404)  # not here yet
        local = mark_local(self.film)
        master = self.film.projection_file()
        media_url = self.client.get(player_data).json()["media_url"]
        self.assertEqual(
            media_url, reverse("ic_local_file", args=[master.pk, master.checksum])
        )
        response = self.client.get(media_url, HTTP_RANGE="bytes=0-9")
        self.assertEqual(response.status_code, 206)
        self.assertIn("immutable", response["Cache-Control"])
        master_bytes = Path(master.file.path).read_bytes()
        self.assertEqual(b"".join(response.streaming_content), master_bytes[:10])
        local.xxh64 = "0000000000000000"
        local.save()
        self.assertEqual(self.client.get(media_url).status_code, 404)

    def test_a_sync_replacing_the_master_cannot_change_a_playing_film(self):
        """The output keeps the URL it loaded; that URL always serves the same bytes."""
        mark_local(self.film)
        old_url = self.client.get(
            reverse("ic_item_player_data", args=[self.film.pk])
        ).json()["media_url"]
        old_bytes = b"".join(
            self.client.get(old_url, HTTP_RANGE="bytes=0-99").streaming_content
        )

        # A sync applies mid-film: the item's resource now has a different master,
        # which hasn't even finished downloading.
        resource = self.film.content.resource
        old_master = self.film.projection_file()
        old_master.is_projection_file = False
        old_master.save()
        new_master = make_master(resource, payload=b"a re-graded master " * 500)

        response = self.client.get(old_url, HTTP_RANGE="bytes=0-99")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b"".join(response.streaming_content), old_bytes)
        # Loading the item again (the next showing) waits for the new master.
        self.assertEqual(
            self.client.get(
                reverse("ic_item_player_data", args=[self.film.pk])
            ).status_code,
            404,
        )
        mark_local(self.film)
        self.assertEqual(
            self.client.get(reverse("ic_item_player_data", args=[self.film.pk])).json()[
                "media_url"
            ],
            reverse("ic_local_file", args=[new_master.pk, new_master.checksum]),
        )

    def test_unknown_files_are_not_served(self):
        other = make_master(ResourceFactory())
        self.assertEqual(
            self.client.get(
                reverse("ic_local_file", args=[other.pk, other.checksum])
            ).status_code,
            404,
        )

    def test_unplayable_items_are_flagged_on_the_controller(self):
        response = self.client.get(reverse("ic_control", args=[self.program.pk]))
        items = response.context["page_data"]["items"]
        self.assertFalse(any(item["playable"] for item in items))
        mark_local(self.film)
        items = self.client.get(reverse("ic_control", args=[self.program.pk])).context[
            "page_data"
        ]["items"]
        self.assertEqual([i["playable"] for i in items], [False, True])

    def test_past_programs_do_not_play_but_upcoming_ones_rehearse(self):
        today = timezone.localdate()
        self.program.start_date = self.program.end_date = today - timedelta(days=2)
        self.program.save()
        self.assertEqual(
            self.client.get(reverse("ic_control", args=[self.program.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("ic_item_player_data", args=[self.film.pk])
            ).status_code,
            404,
        )
        self.program.start_date = self.program.end_date = today + timedelta(days=2)
        self.program.save()
        response = self.client.get(reverse("ic_control", args=[self.program.pk]))
        self.assertTrue(response.context["rehearsal"])

    def test_unsafe_methods_are_refused_except_sync_now(self):
        for url in (
            reverse("ic_control_index"),
            reverse("ic_output"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 405)

    def test_sync_now_needs_a_live_agent(self):
        response = self.client.post(reverse("ic_sync_run_create"))
        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "systemctl --user restart ic-sync-agent", response.content.decode()
        )
        self.assertFalse(ICSyncRun.objects.exists())

    def test_sync_now_queues_one_forced_run_and_shows_its_progress(self):
        booth_state.record_agent_heartbeat()
        response = self.client.post(reverse("ic_sync_run_create"), {"full_rehash": "1"})
        self.assertEqual(response.status_code, 200)
        run = ICSyncRun.objects.get()
        self.assertEqual(run.trigger, ICSyncRun.Trigger.FORCE_PULL)
        self.assertEqual(run.requested_by, "booth operator")
        self.assertTrue(run.options["full_rehash"])
        self.assertContains(
            response, f'hx-get="{reverse("ic_sync_run_detail", args=[run.pk])}"'
        )
        self.client.post(reverse("ic_sync_run_create"))
        self.assertEqual(ICSyncRun.objects.count(), 1)
        self.assertTrue(ICSyncRun.objects.get().options["full_rehash"])
        detail = self.client.get(
            reverse("ic_sync_run_detail", args=[run.pk]), HTTP_ACCEPT="application/json"
        )
        self.assertEqual(detail.json()["status"], "queued")
        run.status = ICSyncRun.Status.FAILED
        run.error = "Production unreachable."
        run.save()
        html = self.client.get(reverse("ic_sync_run_detail", args=[run.pk]))
        self.assertContains(html, "Production unreachable.")
        self.assertNotContains(html, "every 1s")

    def test_csrf_protects_sync_now(self):
        booth_state.record_agent_heartbeat()
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse("ic_sync_run_create")).status_code, 403)


class BoothViewsOnProductionTests(ICTempDirsMixin, TestCase):
    def test_booth_views_refuse_to_run_on_production(self):
        from django.test import RequestFactory

        from ic import views_booth

        request = RequestFactory().get("/ic/")
        from django.http import Http404

        with self.assertRaises(Http404):
            views_booth.control_index(request)


@override_settings(
    IS_IC_INSTANCE=True, ROOT_URLCONF="yvideo.urls_ic", MIDDLEWARE=BOOTH_MIDDLEWARE
)
class KioskStaticTests(ICTempDirsMixin, TestCase):
    def test_kiosk_serves_collected_static_files_without_traversal(self):
        static_root = self._ic_tmp / "static"
        (static_root / "ic" / "css").mkdir(parents=True)
        (static_root / "ic" / "css" / "dark.css").write_text("body {}")
        (self._ic_tmp / "secret.txt").write_text("nope")
        with self.settings(STATIC_ROOT=static_root):
            response = self.client.get("/static/ic/css/dark.css")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"body {}")
            self.assertIn(
                self.client.get("/static/../secret.txt").status_code, (400, 404)
            )
