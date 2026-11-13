"""The booth: userless, read-only kiosk and file-addressed media."""

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from ic.models import ICBoothState
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
]


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
        self.assertEqual(self.client.get("/").status_code, 200)

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
        # A late show: after midnight, yesterday's program still plays.
        self.program.start_date = self.program.end_date = today - timedelta(days=1)
        self.program.save()
        mark_local(self.film)
        self.assertEqual(
            self.client.get(reverse("ic_control", args=[self.program.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("ic_item_player_data", args=[self.film.pk])
            ).status_code,
            200,
        )
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

    def test_the_controller_learns_when_newer_data_was_applied(self):
        from ic.models import ICBoothState

        mark_local(self.film)
        state = ICBoothState.get()
        state.applied_at = timezone.now() - timedelta(hours=1)
        state.save()
        page = self.client.get(reverse("ic_control", args=[self.program.pk]))
        version = state.applied_at.isoformat()
        self.assertEqual(page.context["page_data"]["data_version"], version)
        self.assertContains(page, f'data-ic-data-version="{version}"')
        self.assertContains(page, 'id="ic-stale"')
        state.applied_at = timezone.now()
        state.save()
        badge = self.client.get(reverse("ic_sync_badge"))
        self.assertContains(
            badge, f'data-ic-data-version="{state.applied_at.isoformat()}"'
        )

    def test_sync_now_needs_a_live_agent(self):
        response = self.client.post(reverse("ic_sync_run_create"))
        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "systemctl --user restart ic-sync-agent", response.content.decode()
        )
        self.assertFalse(ICSyncRun.objects.exists())

    def test_sync_now_queues_one_forced_run_and_shows_its_progress(self):
        ICBoothState.record_agent_heartbeat()
        response = self.client.post(reverse("ic_sync_run_create"))
        self.assertEqual(response.status_code, 200)
        run = ICSyncRun.objects.get()
        self.assertEqual(run.trigger, ICSyncRun.Trigger.FORCE_PULL)
        # The operator can't start a full re-hash: it would compete with playback.
        self.assertFalse(run.full_rehash)
        self.assertContains(
            response, f'hx-get="{reverse("ic_sync_run_detail", args=[run.pk])}"'
        )
        self.client.post(reverse("ic_sync_run_create"))
        self.assertEqual(ICSyncRun.objects.count(), 1)
        run.status = ICSyncRun.Status.FAILED
        run.error = "Production unreachable."
        run.save()
        html = self.client.get(reverse("ic_sync_run_detail", args=[run.pk]))
        self.assertContains(html, "Production unreachable.")
        self.assertNotContains(html, "every 1s")


class BoothViewsOnProductionTests(ICTempDirsMixin, TestCase):
    def test_booth_views_refuse_to_run_on_production(self):
        from django.test import RequestFactory

        from ic import views_player

        request = RequestFactory().get("/ic/status/")
        from django.http import Http404

        with self.assertRaises(Http404):
            views_player.status(request)
