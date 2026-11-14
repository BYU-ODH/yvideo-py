"""IC views on production: management, the preview player, and the sync API."""

import json
from pathlib import Path
import re
import uuid

from django.core.cache import cache
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from reversion.models import Version

from core.factories import PlaylistFactory
from core.factories import ResourceFactory
from core.factories import UserFactory
from core.models import ResourceFile
from ic.models import ICBoothReport
from ic.models import ICProgram
from ic.models import ICProgramItem
from ic.sync.bundle import build_bundle
from ic.testing import ICTempDirsMixin
from ic.testing import make_ic_editor
from ic.testing import make_master
from ic.testing import make_program_scenario


class ManagementAccessTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.program = self.scenario["program"]
        self.item = self.scenario["film_item"]

    def test_only_ic_editors_get_in(self):
        p, i = self.program.pk, self.item.pk
        self.client.force_login(UserFactory(instructor=True))
        for method, url in (
            ("get", reverse("ic_manage_dashboard")),
            ("post", reverse("ic_program_delete", args=[p])),
            ("get", reverse("ic_control", args=[p])),
            ("get", reverse("ic_item_media", args=[i])),
        ):
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.assertTrue(ICProgram.objects.filter(pk=p).exists())


class ProgramEditingTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.program = self.scenario["program"]
        self.client.force_login(self.scenario["editor"])

    def test_create_program_records_a_revision(self):
        today = timezone.localdate().isoformat()
        response = self.client.post(
            reverse("ic_program_new"),
            {"name": "Parasite", "start_date": today, "end_date": today, "notes": ""},
        )
        program = ICProgram.objects.get(name="Parasite")
        self.assertRedirects(response, reverse("ic_program_edit", args=[program.pk]))
        self.assertEqual(Version.objects.get_for_object(program).count(), 1)

    def test_end_before_start_is_rejected(self):
        response = self.client.post(
            reverse("ic_program_new"),
            {"name": "Bad", "start_date": "2026-11-12", "end_date": "2026-11-10"},
        )
        self.assertContains(
            response, "The program can&#x27;t end before it starts.", status_code=400
        )
        self.assertFalse(ICProgram.objects.filter(name="Bad").exists())

    def test_add_move_and_remove_items(self):
        extra = self.scenario["feature"]
        self.client.post(
            reverse("ic_item_add", args=[self.program.pk]),
            {"content_id": extra.pk, "mode": "once"},
        )
        self.assertEqual(self.program.items.count(), 3)
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

    def test_item_settings_save_as_they_change(self):
        """The program page posts each change at once; nothing waits for a Save button."""
        item = self.scenario["film_item"]
        prefix = f"item-{item.pk}"
        page = self.client.get(reverse("ic_program_edit", args=[self.program.pk]))
        update_url = reverse("ic_item_update", args=[self.program.pk, item.pk])
        self.assertContains(page, f'hx-post="{update_url}"')
        self.assertContains(page, 'hx-trigger="change"')
        response = self.client.post(
            update_url,
            {
                f"{prefix}-mode": "once",
                f"{prefix}-auto_advance": "on",
                f"{prefix}-subtitles": "burned_in",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertRedirects(
            response, reverse("ic_program_edit", args=[self.program.pk])
        )
        item.refresh_from_db()
        self.assertTrue(item.auto_advance)
        self.assertEqual(item.subtitles, ICProgramItem.Subtitles.BURNED_IN)

    def test_edit_and_delete_a_program(self):
        url = reverse("ic_program_edit", args=[self.program.pk])
        response = self.client.post(
            url,
            {
                "name": "Amélie, extended",
                "start_date": self.program.start_date.isoformat(),
                "end_date": self.program.end_date.isoformat(),
                "notes": "",
            },
        )
        self.assertRedirects(response, url)
        self.program.refresh_from_db()
        self.assertEqual(self.program.name, "Amélie, extended")
        response = self.client.post(
            reverse("ic_program_delete", args=[self.program.pk])
        )
        self.assertRedirects(response, reverse("ic_manage_dashboard"))
        self.assertFalse(ICProgram.objects.filter(pk=self.program.pk).exists())


class MasterUploadPathTests(ICTempDirsMixin, TestCase):
    """A master can be uploaded before any IC content uses the film."""

    def setUp(self):
        super().setUp()
        self.admin = UserFactory(admin=True)
        self.client.force_login(self.admin)
        self.new_film = ResourceFactory(name="Tokyo Story")

    def test_find_a_film_without_ic_content_and_list_it_once_it_has_a_master(self):
        listing = self.client.get(reverse("ic_manage_resources"))
        self.assertNotContains(listing, "Tokyo Story")
        found = self.client.get(reverse("ic_manage_resources"), {"q": "tokyo"})
        self.assertContains(found, "Tokyo Story")
        self.assertContains(
            found, reverse("ic_projection_upload", args=[self.new_film.pk])
        )
        make_master(self.new_film)
        listing = self.client.get(reverse("ic_manage_resources"))
        self.assertContains(listing, "Tokyo Story")

    def test_an_ic_editor_without_access_cannot_upload(self):
        editor = make_ic_editor()
        self.client.force_login(editor)
        found = self.client.get(reverse("ic_manage_resources"), {"q": "tokyo"})
        self.assertContains(found, "Tokyo Story")
        self.assertContains(found, "You don't have access to this resource.")
        self.assertEqual(
            self.client.get(
                reverse("ic_projection_upload", args=[self.new_film.pk])
            ).status_code,
            403,
        )


class PreviewPlayerTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.client.force_login(self.scenario["editor"])

    def test_player_data_uses_the_items_subtitle_choice(self):
        film = self.scenario["film_item"]
        data = self.client.get(reverse("ic_item_player_data", args=[film.pk])).json()
        self.assertEqual(len(data["subtitleTracks"]), 1)
        self.assertTrue(data["subtitleTracks"][0]["default"])
        self.assertEqual(data["media_url"], reverse("ic_item_media", args=[film.pk]))
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


class PlaylistToggleTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.playlist = self.scenario["playlist"]

    def test_only_ic_editors_can_mark_a_playlist(self):
        for user, expected in (
            (make_ic_editor(), True),
            (UserFactory(instructor=True), False),
        ):
            playlist = PlaylistFactory(owner=user)
            self.client.force_login(user)
            self.client.post(
                reverse("update_playlist_settings", args=[playlist.pk]),
                {"name": playlist.name, "is_ic": "on"},
            )
            playlist.refresh_from_db()
            self.assertEqual(playlist.is_ic, expected)


@override_settings(IC_SYNC_TOKEN="booth-token")
class SyncAPITests(ICTempDirsMixin, TestCase):
    token = "booth-token"

    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()

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
        self.assertIsNone(ICBoothReport.get().last_seen_at)
        self.assertEqual(self.get("ic_sync_check").status_code, 200)
        self.assertIsNotNone(ICBoothReport.get().last_seen_at)

    def test_check_returns_only_the_manifest_hash(self):
        digest = self.get("ic_sync_manifest").json()["manifest_hash"]
        data = self.get("ic_sync_check").json()
        self.assertEqual(data, {"manifest_hash": digest})

    def post(self, name, data):
        return self.client.post(
            reverse(name),
            data=json.dumps(data),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_masters_outside_syncable_programs_are_refused(self):
        master = self.scenario["film_item"].projection_file()
        ICProgram.objects.all().delete()
        self.assertEqual(
            self.get("ic_sync_projection_file", master.pk).status_code, 403
        )

    def report(self, xxh64):
        master = self.scenario["film_item"].projection_file()
        return master, {
            "run_id": str(uuid.uuid4()),
            "status": "succeeded",
            "applied_manifest_hash": "abc",
            "verified_files": [{"resource_file_id": master.pk, "xxh64": xxh64}],
        }

    def test_status_report_is_kept_and_releases_the_master(self):
        master, report = self.report(
            self.scenario["film_item"].projection_file().checksum
        )
        response = self.post("ic_sync_status", report)
        self.assertEqual(response.status_code, 200)
        master.refresh_from_db()
        self.assertIsNotNone(master.master_released_at)
        booth = ICBoothReport.get()
        self.assertEqual(booth.report["status"], "succeeded")
        self.assertEqual(booth.applied_manifest_hash, "abc")

    def test_a_copy_with_another_checksum_never_releases_the_master(self):
        master, report = self.report("bad")
        self.post("ic_sync_status", report)
        master.refresh_from_db()
        self.assertIsNone(master.master_released_at)


class DashboardTests(ICTempDirsMixin, TestCase):
    """What IC editors see about the booth, from its last report."""

    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.client.force_login(self.scenario["editor"])

    def test_the_booth_section_and_readiness_follow_its_last_report(self):
        masters = ResourceFile.objects.filter(is_projection_file=True)
        booth = ICBoothReport.objects.create(
            pk=1,
            last_seen_at=timezone.now(),
            report={"failed_files": [{"name": "x", "error": "Disk full"}]},
        )
        page = self.client.get(reverse("ic_manage_dashboard"))
        self.assertContains(page, "Disk full")
        self.assertContains(page, "have a verified copy of this master yet")

        booth.report = {
            "applied_manifest_hash": build_bundle()[1],
            "verified_files": [
                {"resource_file_id": m.pk, "xxh64": m.checksum} for m in masters
            ],
        }
        booth.save()
        page = self.client.get(reverse("ic_manage_dashboard"))
        self.assertNotContains(page, "have a verified copy")
        self.assertContains(page, "✅ Yes")

        program = self.scenario["program"]
        program.items.first().delete()
        cache.clear()  # the manifest hash is cached for a few seconds
        page = self.client.get(reverse("ic_manage_dashboard"))
        self.assertContains(page, "Production has changes the booth")


class UseSDFileTests(ICTempDirsMixin, TestCase):
    def test_ic_contents_switch_to_the_sd_file_made_from_the_master(self):
        scenario = make_program_scenario()
        content = scenario["feature"]
        sd = content.resource_file
        content.resource_file = ResourceFile.objects.create(
            resource=content.resource, version="dvd rip", file="x/dvd.mp4", checksum="1"
        )
        content.save()
        self.client.force_login(UserFactory(admin=True))
        response = self.client.post(
            reverse("ic_resource_use_sd", args=[content.resource_id])
        )
        self.assertRedirects(response, reverse("ic_manage_resources"))
        content.refresh_from_db()
        self.assertEqual(content.resource_file, sd)


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
        self.assertIn("International Cinema program", response.content.decode())
        response = self.client.delete(
            reverse("delete_playlist", args=[self.scenario["playlist"].pk])
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(feature.__class__.objects.filter(pk=feature.pk).exists())
