"""IC views on production: management, the preview player, and the sync API."""

import json
from pathlib import Path
import re
from unittest import mock
import uuid

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from reversion.models import Version

from core.factories import PlaylistFactory
from core.factories import UserFactory
from ic.models import ICInstance
from ic.models import ICInstanceFile
from ic.models import ICProgram
from ic.models import ICProgramItem
from ic.models import ICSyncRequest
from ic.models import MediaJob
from ic.sync.bundle import build_bundle
from ic.sync.push import AgentError
from ic.testing import ICTempDirsMixin
from ic.testing import make_ic_editor
from ic.testing import make_program_scenario


class ManagementAccessTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.program = self.scenario["program"]
        self.item = self.scenario["film_item"]

    def urls(self):
        p, i = self.program.pk, self.item.pk
        return [
            ("get", reverse("ic_manage_dashboard")),
            ("get", reverse("ic_manage_resources")),
            ("get", reverse("ic_program_new")),
            ("post", reverse("ic_program_create")),
            ("get", reverse("ic_program_edit", args=[p])),
            ("post", reverse("ic_program_update", args=[p])),
            ("post", reverse("ic_program_delete", args=[p])),
            ("post", reverse("ic_item_add", args=[p])),
            ("post", reverse("ic_item_update", args=[p, i])),
            ("post", reverse("ic_item_move", args=[p, i])),
            ("post", reverse("ic_item_delete", args=[p, i])),
            ("post", reverse("ic_items_reorder", args=[p])),
            ("get", reverse("ic_control_index")),
            ("get", reverse("ic_control", args=[p])),
            ("get", reverse("ic_output")),
            ("get", reverse("ic_item_player_data", args=[i])),
            ("get", reverse("ic_item_media", args=[i])),
        ]

    def test_only_ic_editors_get_in(self):
        for user in (
            UserFactory(instructor=True),
            UserFactory(lab_assistant=True),
            UserFactory(),
        ):
            self.client.force_login(user)
            for method, url in self.urls():
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.assertTrue(ICProgram.objects.filter(pk=self.program.pk).exists())

    def test_ic_editors_and_superusers_see_the_pages(self):
        for user in (self.scenario["editor"], UserFactory(admin=True)):
            self.client.force_login(user)
            for url in (
                reverse("ic_manage_dashboard"),
                reverse("ic_manage_resources"),
                reverse("ic_program_edit", args=[self.program.pk]),
                reverse("ic_control_index"),
                reverse("ic_control", args=[self.program.pk]),
                reverse("ic_output"),
            ):
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)


class ProgramEditingTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.program = self.scenario["program"]
        self.client.force_login(self.scenario["editor"])

    def test_create_program_records_a_revision(self):
        today = timezone.localdate().isoformat()
        response = self.client.post(
            reverse("ic_program_create"),
            {"name": "Parasite", "start_date": today, "end_date": today, "notes": ""},
        )
        program = ICProgram.objects.get(name="Parasite")
        self.assertRedirects(response, reverse("ic_program_edit", args=[program.pk]))
        self.assertEqual(Version.objects.get_for_object(program).count(), 1)

    def test_end_before_start_is_rejected(self):
        response = self.client.post(
            reverse("ic_program_create"),
            {"name": "Bad", "start_date": "2026-11-12", "end_date": "2026-11-10"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ICProgram.objects.filter(name="Bad").exists())

    def test_add_reorder_move_and_remove_items(self):
        extra = self.scenario["feature"]
        self.client.post(
            reverse("ic_item_add", args=[self.program.pk]),
            {"content_id": extra.pk, "mode": "once"},
        )
        items = list(self.program.items.order_by("position"))
        self.assertEqual(len(items), 3)
        reversed_ids = [i.pk for i in reversed(items)]
        response = self.client.post(
            reverse("ic_items_reorder", args=[self.program.pk]),
            data=json.dumps({"item_ids": reversed_ids}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            [i.pk for i in self.program.items.order_by("position")], reversed_ids
        )
        bad = self.client.post(
            reverse("ic_items_reorder", args=[self.program.pk]),
            data=json.dumps({"item_ids": reversed_ids[:2]}),
            content_type="application/json",
        )
        self.assertEqual(bad.status_code, 400)
        first = self.program.items.order_by("position").first()
        self.client.post(
            reverse("ic_item_move", args=[self.program.pk, first.pk]),
            {"direction": "down"},
        )
        self.assertEqual(self.program.items.order_by("position")[1].pk, first.pk)
        self.client.post(reverse("ic_item_delete", args=[self.program.pk, first.pk]))
        self.assertEqual(
            list(
                self.program.items.values_list("position", flat=True).order_by(
                    "position"
                )
            ),
            [0, 1],
        )

    def test_only_ic_playlist_contents_can_be_added(self):
        from core.factories import ContentFactory

        outside = ContentFactory(playlist=PlaylistFactory(is_ic=False))
        response = self.client.post(
            reverse("ic_item_add", args=[self.program.pk]), {"content_id": outside.pk}
        )
        self.assertEqual(response.status_code, 404)

    def test_update_item_settings(self):
        item = self.scenario["film_item"]
        prefix = f"item-{item.pk}"
        self.client.post(
            reverse("ic_item_update", args=[self.program.pk, item.pk]),
            {
                f"{prefix}-mode": "once",
                f"{prefix}-auto_advance": "on",
                f"{prefix}-subtitles": "burned_in",
            },
        )
        item.refresh_from_db()
        self.assertTrue(item.auto_advance)
        self.assertEqual(item.subtitles, ICProgramItem.Subtitles.BURNED_IN)

    def test_an_item_of_another_program_is_not_found(self):
        other = ICProgram.objects.create(
            name="other", start_date=timezone.localdate(), end_date=timezone.localdate()
        )
        item = self.scenario["film_item"]
        response = self.client.post(reverse("ic_item_delete", args=[other.pk, item.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ICProgramItem.objects.filter(pk=item.pk).exists())


class PreviewPlayerTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.client.force_login(self.scenario["editor"])

    def test_control_page_embeds_the_queue(self):
        response = self.client.get(
            reverse("ic_control", args=[self.scenario["program"].pk])
        )
        self.assertContains(response, 'id="ic-page-data"')
        data = response.context["page_data"]
        self.assertEqual([i["mode"] for i in data["items"]], ["loop", "once"])
        self.assertIsNone(data["heartbeat_url"])
        self.assertFalse(data["booth"])

    def test_player_data_uses_the_items_subtitle_choice(self):
        film = self.scenario["film_item"]
        data = self.client.get(reverse("ic_item_player_data", args=[film.pk])).json()
        self.assertEqual(len(data["subtitleTracks"]), 1)
        self.assertTrue(data["subtitleTracks"][0]["default"])
        self.assertEqual(len(data["fingerprint"]), 64)
        self.assertTrue(any(a["type"] == "blur" for a in data["annotations"]))
        film.subtitles = ICProgramItem.Subtitles.BURNED_IN
        film.save()
        data = self.client.get(reverse("ic_item_player_data", args=[film.pk])).json()
        self.assertEqual(data["subtitleTracks"], [])

    def test_preview_streams_the_sd_file_never_the_master(self):
        film = self.scenario["film_item"]
        response = self.client.get(
            reverse("ic_item_media", args=[film.pk]), HTTP_RANGE="bytes=0-3"
        )
        self.assertEqual(response.status_code, 206)
        body = b"".join(response.streaming_content)
        self.assertEqual(
            body, Path(film.content.resource_file.file.path).read_bytes()[:4]
        )
        self.assertNotEqual(
            body, Path(film.projection_file().file.path).read_bytes()[:4]
        )

    def test_output_page_loads_nothing_from_the_network(self):
        program = self.scenario["program"]
        for url in (
            reverse("ic_output"),
            reverse("ic_control", args=[program.pk]),
            reverse("ic_control_index"),
        ):
            html = self.client.get(url).content.decode()
            loaded = re.findall(r'(?:src|href)="([^"]+)"', html)
            with self.subTest(url=url):
                self.assertTrue(loaded)
                self.assertEqual(
                    [u for u in loaded if re.match(r"(https?:)?//", u)], []
                )
        self.assertIn(
            "INTERNATIONAL CINEMA",
            self.client.get(reverse("ic_output")).content.decode(),
        )


class PlaylistToggleTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.playlist = self.scenario["playlist"]

    def post_settings(self, user, **extra):
        self.client.force_login(user)
        data = {"name": self.playlist.name, "published": "", "archived": ""}
        data.update(extra)
        return self.client.post(
            reverse("update_playlist_settings", args=[self.playlist.pk]), data
        )

    def test_ic_editor_can_mark_a_playlist(self):
        editor = make_ic_editor()
        playlist = PlaylistFactory(owner=editor)
        self.client.force_login(editor)
        self.client.post(
            reverse("update_playlist_settings", args=[playlist.pk]),
            {"name": playlist.name, "is_ic": "on", "is_ic_present": "1"},
        )
        playlist.refresh_from_db()
        self.assertTrue(playlist.is_ic)

    def test_other_users_cannot_flip_the_flag(self):
        instructor = UserFactory(instructor=True)
        playlist = PlaylistFactory(owner=instructor)
        self.client.force_login(instructor)
        self.client.post(
            reverse("update_playlist_settings", args=[playlist.pk]),
            {"name": playlist.name, "is_ic": "on", "is_ic_present": "1"},
        )
        playlist.refresh_from_db()
        self.assertFalse(playlist.is_ic)

    def test_unsetting_is_refused_while_scheduled(self):
        self.post_settings(self.scenario["editor"], is_ic_present="1")
        self.playlist.refresh_from_db()
        self.assertTrue(self.playlist.is_ic)

    def test_header_link_for_ic_editors_only(self):
        self.client.force_login(self.scenario["editor"])
        self.assertContains(
            self.client.get(reverse("playlists")), reverse("ic_manage_dashboard")
        )
        self.client.force_login(UserFactory(instructor=True))
        self.assertNotContains(
            self.client.get(reverse("playlists")), reverse("ic_manage_dashboard")
        )


class SyncAPITests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.instance = ICInstance(name="booth")
        self.token = self.instance.issue_token()
        self.instance.save()

    def get(self, name, *args, token=None, **headers):
        return self.client.get(
            reverse(name, args=args),
            HTTP_AUTHORIZATION=f"Bearer {token if token is not None else self.token}",
            **headers,
        )

    def test_token_required(self):
        self.assertEqual(self.client.get(reverse("ic_sync_manifest")).status_code, 401)
        self.assertEqual(self.get("ic_sync_manifest", token="wrong").status_code, 401)
        self.assertEqual(self.get("ic_sync_manifest").status_code, 200)
        self.instance.refresh_from_db()
        self.assertIsNotNone(self.instance.last_seen_at)

    def test_manifest_etag_and_fixture(self):
        response = self.get("ic_sync_manifest")
        etag = response["ETag"]
        self.assertEqual(response.json()["manifest_hash"], etag.strip('"'))
        self.assertEqual(
            self.get("ic_sync_manifest", HTTP_IF_NONE_MATCH=etag).status_code, 304
        )
        fixture = self.get("ic_sync_fixture")
        self.assertEqual(fixture["X-IC-Manifest-Hash"], etag.strip('"'))
        self.assertEqual(
            self.get("ic_sync_manifest_hash").json()["manifest_hash"], etag.strip('"')
        )

    def test_master_download_supports_large_ranges(self):
        master = self.scenario["film_item"].projection_file()
        response = self.get(
            "ic_sync_projection_file", master.pk, HTTP_RANGE="bytes=10-"
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(
            b"".join(response.streaming_content),
            Path(master.file.path).read_bytes()[10:],
        )

    def test_masters_outside_syncable_programs_are_refused(self):
        master = self.scenario["film_item"].projection_file()
        ICProgram.objects.all().delete()
        self.assertEqual(
            self.get("ic_sync_projection_file", master.pk).status_code, 403
        )

    def test_released_masters_are_gone(self):
        master = self.scenario["film_item"].projection_file()
        master.master_released_at = timezone.now()
        master.save()
        self.assertEqual(
            self.get("ic_sync_projection_file", master.pk).status_code, 404
        )

    def test_subtitle_download(self):
        subtitle = self.scenario["feature"].default_subtitle_track
        response = self.get("ic_sync_subtitle_file", subtitle.pk)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"WEBVTT"))

    def test_status_report_records_copies_and_queues_release(self):
        master = self.scenario["film_item"].projection_file()
        sync_request = ICSyncRequest.objects.create(
            instance=self.instance, status="queued"
        )
        report = {
            "run_id": str(uuid.uuid4()),
            "request_id": str(sync_request.request_id),
            "status": "succeeded",
            "applied_manifest_hash": "abc",
            "verified_files": [
                {
                    "kind": "projection",
                    "resource_file_id": master.pk,
                    "xxh64": master.checksum,
                    "verified_at": timezone.now().isoformat(),
                    "hashed_this_run": True,
                }
            ],
        }
        response = self.client.post(
            reverse("ic_sync_status"),
            data=json.dumps(report),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(response.status_code, 200)
        copy = ICInstanceFile.objects.get(instance=self.instance, resource_file=master)
        self.assertTrue(copy.hashed_from_bytes)
        self.assertTrue(
            MediaJob.objects.filter(
                kind=MediaJob.Kind.RELEASE_MASTER, resource_file=master
            ).exists()
        )
        sync_request.refresh_from_db()
        self.assertEqual(sync_request.status, "succeeded")
        self.assertIsNotNone(sync_request.finished_at)
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.last_applied_manifest_hash, "abc")

    def test_a_mismatched_report_never_counts_as_hashed(self):
        master = self.scenario["film_item"].projection_file()
        report = {
            "verified_files": [
                {
                    "kind": "projection",
                    "resource_file_id": master.pk,
                    "xxh64": "bad",
                    "hashed_this_run": True,
                }
            ]
        }
        self.client.post(
            reverse("ic_sync_status"),
            data=json.dumps(report),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertFalse(
            ICInstanceFile.objects.get(resource_file=master).hashed_from_bytes
        )
        self.assertFalse(
            MediaJob.objects.filter(kind=MediaJob.Kind.RELEASE_MASTER).exists()
        )


class ForcePushTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.instance = ICInstance(name="booth", agent_url="http://booth.test:8443")
        self.instance.issue_token()
        self.instance.save()
        self.client.force_login(self.scenario["editor"])

    def push(self):
        with self.settings(IC_INSTANCE_AGENT_SECRETS={"booth": "s3cret"}):
            return self.client.post(
                reverse("ic_instance_push", args=[self.instance.pk]),
                {"full_rehash": "1"},
            )

    def test_push_sends_a_signed_trigger_and_reports_progress(self):
        run_id = str(uuid.uuid4())
        with mock.patch("ic.views_manage.send_trigger", return_value=run_id) as sent:
            response = self.push()
        self.assertEqual(response.status_code, 200)
        sync_request = ICSyncRequest.objects.get()
        self.assertEqual(str(sync_request.booth_run_id), run_id)
        self.assertEqual(sent.call_args.kwargs["options"]["full_rehash"], True)
        self.assertEqual(sent.call_args.kwargs["trigger"], "force_push")

        digest = build_bundle()[2]
        live = {
            "status": "succeeded",
            "phase": "",
            "report": {"applied_manifest_hash": digest, "programs": []},
            "error": "",
        }
        with (
            self.settings(IC_INSTANCE_AGENT_SECRETS={"booth": "s3cret"}),
            mock.patch("ic.views_manage.fetch_run", return_value=live),
        ):
            detail = self.client.get(
                reverse("ic_sync_request_detail", args=[sync_request.pk])
            )
        self.assertContains(detail, "Booth now matches production")
        sync_request.refresh_from_db()
        self.assertTrue(sync_request.is_final)

    def test_unreachable_booth_fails_immediately(self):
        with mock.patch(
            "ic.views_manage.send_trigger",
            side_effect=AgentError(
                "Booth unreachable: connection refused at booth.test:8443."
            ),
        ):
            response = self.push()
        self.assertContains(response, "connection refused")
        self.assertEqual(ICSyncRequest.objects.get().status, "unreachable")

    def test_lost_contact_mid_run(self):
        with mock.patch("ic.views_manage.send_trigger", return_value=str(uuid.uuid4())):
            self.push()
        sync_request = ICSyncRequest.objects.get()
        with (
            self.settings(IC_INSTANCE_AGENT_SECRETS={"booth": "s3cret"}),
            mock.patch(
                "ic.views_manage.fetch_run", side_effect=AgentError("Booth unreachable")
            ),
        ):
            detail = self.client.get(
                reverse("ic_sync_request_detail", args=[sync_request.pk])
            )
        self.assertContains(detail, "Lost contact with the booth")
        self.assertContains(detail, 'hx-trigger="every 2s"')

    def test_non_ic_users_cannot_push(self):
        self.client.force_login(UserFactory(instructor=True))
        self.assertEqual(
            self.client.post(
                reverse("ic_instance_push", args=[self.instance.pk])
            ).status_code,
            403,
        )


class NotifyTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.instance = ICInstance(name="booth", agent_url="http://booth.test:8443")
        self.instance.issue_token()
        self.instance.save()

    def test_edits_schedule_one_debounced_notification(self):
        from core.models import MuteAnnotation

        with self.captureOnCommitCallbacks(execute=True):
            for annotation in MuteAnnotation.objects.filter(active=True):
                annotation.edit(end_time=annotation.end_time + 1)
                annotation = MuteAnnotation.objects.get(prev=annotation)
                annotation.edit(end_time=annotation.end_time + 1)
        jobs = MediaJob.objects.filter(kind=MediaJob.Kind.IC_NOTIFY)
        self.assertEqual(jobs.count(), 1)
        self.assertGreater(jobs.get().not_before, timezone.now())

    def test_notify_job_triggers_only_when_the_manifest_changed(self):
        from ic import jobs

        job = MediaJob.objects.create(kind=MediaJob.Kind.IC_NOTIFY)
        with (
            self.settings(IC_INSTANCE_AGENT_SECRETS={"booth": "s3cret"}),
            mock.patch("ic.sync.push.send_trigger") as sent,
        ):
            jobs.ic_notify(job)
            jobs.ic_notify(job)
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(sent.call_args.kwargs["trigger"], "event")
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.last_notified_manifest_hash, build_bundle()[2])


class ScheduledContentDeletionTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.client.force_login(self.scenario["editor"])

    def test_deleting_a_scheduled_video_or_playlist_explains_why_not(self):
        feature = self.scenario["feature"]
        response = self.client.delete(reverse("delete_content", args=[feature.pk]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertIn("Amélie week", response.content.decode())
        response = self.client.delete(
            reverse("delete_playlist", args=[self.scenario["playlist"].pk])
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(feature.__class__.objects.filter(pk=feature.pk).exists())
