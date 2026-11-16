"""The booth: userless, read-only kiosk and file-addressed media."""

from datetime import timedelta
import html
import json
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from ic.models import ICBoothState
from ic.models import ICLocalFile
from ic.models import ICProgram
from ic.models import ICSyncRun
from ic.sync.bundle import build_bundle
from ic.testing import ICTempDirsMixin
from ic.testing import apply_bundle
from ic.testing import make_master
from ic.testing import make_program_scenario

BOOTH_MIDDLEWARE = [
    m for m in settings.MIDDLEWARE if m not in settings.BOOTH_EXCLUDED_MIDDLEWARE
]


def mark_local(item):
    master = item.projection_file()
    return ICLocalFile.objects.create(
        xxh64=master.checksum,
        name=master.file.name,
        size=master.size_bytes,
        verified_at=timezone.now(),
        last_program_end=timezone.localdate(),
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
        apply_bundle()

    def test_pages_work_without_any_user_or_program_rows(self):
        mark_local(self.film)
        # The booth plays its bundle; it has no program rows of its own.
        ICProgram.objects.all().delete()
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
        self.assertEqual(media_url, reverse("ic_local_file", args=[master.checksum]))
        response = self.client.get(media_url, HTTP_RANGE="bytes=0-9")
        self.assertEqual(response.status_code, 206)
        self.assertIn("immutable", response["Cache-Control"])
        master_bytes = Path(master.file.path).read_bytes()
        self.assertEqual(b"".join(response.streaming_content), master_bytes[:10])
        local.delete()
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
        apply_bundle()

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
            reverse("ic_local_file", args=[new_master.checksum]),
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
        apply_bundle()
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
        # A bundle from before production dropped the program still holds it.
        self.program.start_date = self.program.end_date = today - timedelta(days=2)
        self.program.save()
        bundle, _ = build_bundle(on=today - timedelta(days=2))
        state = ICBoothState.get()
        state.bundle = bundle
        state.save()
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
        apply_bundle()
        response = self.client.get(reverse("ic_control", args=[self.program.pk]))
        self.assertTrue(response.context["rehearsal"])

    def test_the_controller_learns_when_its_program_changes(self):
        """Only a change to the program it was built from makes the page stale: not a
        run that changed nothing, a change to which masters production holds, or a
        change to another program."""
        mark_local(self.film)
        page = self.client.get(reverse("ic_control", args=[self.program.pk]))
        version = page.context["page_data"]["data_version"]
        self.assertContains(page, 'id="ic-stale"')

        def badge_version():
            badge = self.client.get(reverse("ic_sync_badge")).content.decode()
            marker = badge.split('data-ic-data-versions="')[1].split('"')[0]
            return json.loads(html.unescape(marker)).get(str(self.program.pk))

        self.assertEqual(badge_version(), version)
        state = ICBoothState.get()
        state.applied_at = timezone.now()
        for entry in state.bundle["files"]:
            entry["available_from_parent"] = False
        state.save()
        self.assertEqual(badge_version(), version)
        later = timezone.localdate() + timedelta(days=30)
        ICProgram.objects.create(name="Next month", start_date=later, end_date=later)
        apply_bundle()
        self.assertEqual(badge_version(), version)

        self.film.title = "Renamed"
        self.film.mode = self.film.Mode.LOOP
        self.film.save()
        apply_bundle()
        self.assertNotEqual(badge_version(), version)

    def test_sync_now_queues_one_run_and_shows_its_progress(self):
        # Without a running agent, nothing would ever take the run.
        response = self.client.post(reverse("ic_sync_run_create"))
        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "systemctl --user restart ic-sync-agent", response.content.decode()
        )
        self.assertFalse(ICSyncRun.objects.exists())

        ICBoothState.record_agent_heartbeat()
        response = self.client.post(reverse("ic_sync_run_create"))
        self.assertEqual(response.status_code, 200)
        run = ICSyncRun.objects.get()
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
