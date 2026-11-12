"""The booth: userless kiosk, read-only middleware, and the signed agent surface."""

from datetime import timedelta
import json
from pathlib import Path
import uuid

from django.conf import settings
from django.test import TestCase
from django.test import override_settings
from django.urls import NoReverseMatch
from django.urls import reverse
from django.utils import timezone

from ic import booth_state
from ic.models import ICLocalFile
from ic.models import ICSyncRun
from ic.sync.signing import signed_headers
from ic.testing import ICTempDirsMixin
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
            "ic_agent_sync",
        ):
            with self.subTest(name=name), self.assertRaises(NoReverseMatch):
                reverse(name, args=[1]) if name in (
                    "video_editor",
                    "stream_file",
                ) else reverse(name)

    def test_media_is_the_verified_local_master(self):
        url = reverse("ic_item_media", args=[self.film.pk])
        self.assertEqual(self.client.get(url).status_code, 404)  # not verified yet
        local = mark_local(self.film)
        response = self.client.get(url, HTTP_RANGE="bytes=0-9")
        self.assertEqual(response.status_code, 206)
        master_bytes = Path(self.film.projection_file().file.path).read_bytes()
        self.assertEqual(b"".join(response.streaming_content), master_bytes[:10])
        local.xxh64 = "0000000000000000"
        local.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_unplayable_items_are_flagged_on_the_controller(self):
        response = self.client.get(reverse("ic_control", args=[self.program.pk]))
        items = response.context["page_data"]["items"]
        self.assertFalse(any(item["playable"] for item in items))
        mark_local(self.film)
        items = self.client.get(reverse("ic_control", args=[self.program.pk])).context[
            "page_data"
        ]["items"]
        self.assertEqual([i["playable"] for i in items], [False, True])
        self.assertEqual(
            response.context["page_data"]["heartbeat_url"], reverse("ic_heartbeat")
        )

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

    def test_heartbeat_records_playback_without_a_database_write(self):
        with self.assertNumQueries(0):
            self.client.get(reverse("ic_heartbeat") + "?state=playing")
        self.assertTrue(booth_state.playback_active())
        self.client.get(reverse("ic_heartbeat") + "?state=idle")
        self.assertFalse(booth_state.playback_active())

    def test_unsafe_methods_are_refused_except_sync_now(self):
        for url in (
            reverse("ic_control_index"),
            reverse("ic_heartbeat"),
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


SECRET = "agent-secret"


@override_settings(
    IS_IC_INSTANCE=True,
    ROOT_URLCONF="yvideo.urls_ic_agent",
    MIDDLEWARE=BOOTH_MIDDLEWARE,
    IC_AGENT_SECRET=SECRET,
)
class AgentTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        booth_state.record_agent_heartbeat()

    def trigger(
        self, request_id=None, secret=SECRET, now=None, trigger="force_push", **options
    ):
        path = reverse("ic_agent_sync")
        body = json.dumps(
            {
                "trigger": trigger,
                "request_id": str(request_id or uuid.uuid4()),
                "requested_by": "Ada Editor",
                "options": options,
            }
        ).encode()
        headers = {
            f"HTTP_{k.upper().replace('-', '_')}": v
            for k, v in signed_headers(secret, "POST", path, body, now=now).items()
        }
        return self.client.post(
            path, data=body, content_type="application/json", **headers
        )

    def test_a_signed_trigger_queues_a_run(self):
        response = self.trigger(apply_during_playback=True)
        self.assertEqual(response.status_code, 202)
        run = ICSyncRun.objects.get(pk=response.json()["run_id"])
        self.assertEqual(run.trigger, ICSyncRun.Trigger.FORCE_PUSH)
        self.assertEqual(run.requested_by, "Ada Editor")
        self.assertTrue(run.options["apply_during_playback"])

    def test_replaying_a_request_id_never_creates_a_second_run(self):
        request_id = uuid.uuid4()
        first = self.trigger(request_id).json()["run_id"]
        self.assertEqual(self.trigger(request_id).json()["run_id"], first)
        self.assertEqual(ICSyncRun.objects.count(), 1)

    def test_queued_event_runs_coalesce(self):
        self.trigger(trigger="event")
        self.trigger(trigger="event")
        self.assertEqual(ICSyncRun.objects.filter(trigger="event").count(), 1)

    def test_bad_or_stale_signatures_are_refused(self):
        self.assertEqual(self.trigger(secret="wrong").status_code, 403)
        import time

        self.assertEqual(self.trigger(now=time.time() - 3600).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("ic_agent_sync"), data="{}", content_type="application/json"
            ).status_code,
            403,
        )
        self.assertFalse(ICSyncRun.objects.exists())

    def test_dead_agent_is_reported_immediately(self):
        booth_state.record_agent_heartbeat(now=0)
        response = self.trigger()
        self.assertEqual(response.status_code, 503)
        self.assertIn("isn't running", response.content.decode())

    def test_progress_and_status_are_signed_reads(self):
        run_id = self.trigger().json()["run_id"]
        path = reverse("ic_agent_sync_progress", args=[run_id])
        self.assertEqual(self.client.get(path).status_code, 403)
        headers = {
            f"HTTP_{k.upper().replace('-', '_')}": v
            for k, v in signed_headers(SECRET, "GET", path).items()
        }
        data = self.client.get(path, **headers).json()
        self.assertEqual(data["status"], "queued")
        self.assertTrue(data["agent_alive"])

    def test_only_agent_routes_exist(self):
        for name in ("ic_control_index", "ic_output", "admin:index", "playlists"):
            with self.subTest(name=name), self.assertRaises(NoReverseMatch):
                reverse(name)


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
