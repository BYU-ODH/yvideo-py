"""Production → booth sync: bundle, import, verification, and the agent's run pipeline."""

import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from unittest import mock
import uuid

from django.test import TestCase
from django.test import override_settings
from django.utils import timezone

from core.models import BlurAnchorPoint
from core.models import Content
from core.models import MuteAnnotation
from core.models import ResourceFile
from core.models import User
from ic.models import ICBoothState
from ic.models import ICLocalFile
from ic.models import ICProgram
from ic.models import ICProgramItem
from ic.models import ICSyncRun
from ic.sync import agent
from ic.sync import bundle as bundle_module
from ic.sync.bundle import build_bundle
from ic.sync.bundle import content_fingerprint
from ic.sync.bundle import manifest_hash
from ic.sync.client import ParentError
from ic.sync.importer import FingerprintMismatch
from ic.sync.importer import apply_fixture
from ic.testing import ICTempDirsMixin
from ic.testing import make_film
from ic.testing import make_program_scenario


class BundleTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()

    def test_round_trip_reproduces_identical_player_json(self):
        before = {
            c.pk: c.get_player_json(include_subtitles=True)
            for c in Content.objects.all()
        }
        manifest, fixture, _ = build_bundle()
        apply_fixture(fixture, manifest["fingerprints"])
        after = {
            c.pk: c.get_player_json(include_subtitles=True)
            for c in Content.objects.all()
        }
        self.assertEqual(before, after)
        for content_id, fingerprint in manifest["fingerprints"].items():
            self.assertEqual(
                content_fingerprint(Content.objects.get(pk=int(content_id))),
                fingerprint,
            )

    def test_round_trip_preserves_raw_geometry_and_undo_history(self):
        # Timestamps round to milliseconds in Django's JSON serializer; they are not part
        # of the player data, so they are left out of the comparison.
        def rows(model):
            return [
                {k: v for k, v in row.items() if k not in ("created_at", "updated_at")}
                for row in model.objects.order_by("pk").values()
            ]

        points, mutes = rows(BlurAnchorPoint), rows(MuteAnnotation)
        manifest, fixture, _ = build_bundle()
        apply_fixture(fixture, manifest["fingerprints"])
        self.assertEqual(points, rows(BlurAnchorPoint))
        self.assertEqual(mutes, rows(MuteAnnotation))

    def test_fixture_carries_no_personal_data(self):
        editor = self.scenario["editor"]
        editor.netid = "abc123"
        editor.email = "person@byu.edu"
        editor.save()
        _, fixture, _ = build_bundle()
        rows = json.loads(fixture)
        users = [row for row in rows if row["model"] == "core.user"]
        self.assertTrue(users)
        for row in users:
            fields = row["fields"]
            self.assertNotIn("email", fields)
            self.assertNotIn("netid", fields)
            self.assertTrue(fields["username"].startswith("ic-mirror-"))
            self.assertFalse(fields["is_active"])
            self.assertEqual(fields["password"], "!")
        text = fixture.decode()
        self.assertNotIn("abc123", text)
        self.assertNotIn("person@byu.edu", text)
        self.assertNotIn(editor.username, text)

    def test_fingerprint_mismatch_rolls_back(self):
        manifest, fixture, _ = build_bundle()
        content = self.scenario["feature"]
        tampered = dict(manifest["fingerprints"], **{str(content.pk): "0" * 64})
        count_before = MuteAnnotation.objects.count()
        title_before = Content.objects.get(pk=content.pk).title
        with self.assertRaises(FingerprintMismatch) as caught:
            apply_fixture(fixture, tampered)
        self.assertEqual(caught.exception.content_ids, [content.pk])
        self.assertEqual(MuteAnnotation.objects.count(), count_before)
        self.assertEqual(Content.objects.get(pk=content.pk).title, title_before)
        self.assertTrue(
            User.objects.filter(pk=self.scenario["editor"].pk, is_active=True).exists()
        )

    def test_manifest_hash_is_stable_and_tracks_annotation_edits(self):
        first = build_bundle()[2]
        self.assertEqual(first, build_bundle()[2])
        mute = MuteAnnotation.objects.filter(active=True).first()
        mute.edit(end_time=mute.end_time + 1)
        self.assertNotEqual(first, build_bundle()[2])

    def test_manifest_lists_masters_and_subtitles_with_checksums(self):
        manifest, _, digest = build_bundle()
        self.assertEqual(digest, manifest_hash(manifest))
        kinds = sorted(entry["kind"] for entry in manifest["files"])
        self.assertEqual(kinds, ["projection", "projection", "subtitle"])
        master = self.scenario["film_item"].projection_file()
        entry = next(
            e for e in manifest["files"] if e.get("resource_file_id") == master.pk
        )
        self.assertEqual(entry["xxh64"], master.checksum)
        self.assertEqual(entry["size"], master.size_bytes)
        self.assertTrue(entry["available_from_parent"])
        program = manifest["programs"][0]
        self.assertEqual(program["production_problems"], [])
        self.assertEqual(len(program["resource_ids"]), 2)

    def test_subtitle_checksums_are_not_recomputed_for_every_manifest(self):
        with mock.patch.object(
            bundle_module, "file_xxh64", wraps=bundle_module.file_xxh64
        ) as hashed:
            build_bundle()
            build_bundle()
        self.assertEqual(hashed.call_count, 1)  # the one subtitle, once
        subtitle = self.scenario["feature"].default_subtitle_track
        Path(subtitle.subtitles_file.path).write_text("WEBVTT\n\nchanged\n")
        with mock.patch.object(
            bundle_module, "file_xxh64", wraps=bundle_module.file_xxh64
        ) as hashed:
            manifest = build_bundle()[0]
        self.assertEqual(hashed.call_count, 1)
        entry = next(e for e in manifest["files"] if e["kind"] == "subtitle")
        self.assertEqual(
            entry["xxh64"], bundle_module.file_xxh64(subtitle.subtitles_file.path)
        )

    def test_past_programs_are_not_synced(self):
        program = self.scenario["program"]
        program.start_date = program.end_date = timezone.localdate() - timedelta(days=5)
        program.save()
        manifest, _, _ = build_bundle()
        self.assertEqual(manifest["programs"], [])
        self.assertEqual(manifest["files"], [])


class FakeParent:
    """Stands in for production: serves a captured manifest, fixture and file bytes."""

    def __init__(self, manifest, fixture, digest, files):
        self.manifest_data = manifest
        self.fixture_bytes = fixture
        self.digest = digest
        self.files = files
        self.reports = []
        self.progress = []
        self.downloads = []
        self.download_states = []
        self.sync_requests = []
        self.unreachable = False

    def _reach(self):
        if self.unreachable:
            raise ParentError("Production unreachable: connection refused")

    def check(self):
        self._reach()
        return {"manifest_hash": self.digest, "sync_requests": self.sync_requests}

    def manifest(self, if_none_match=None):
        self._reach()
        if if_none_match and if_none_match == self.digest:
            return None, if_none_match
        return copy.deepcopy(self.manifest_data), self.digest

    def fixture(self):
        return self.fixture_bytes

    def download(self, entry, part_path, *, on_bytes=None, throttled=lambda: False):
        self.downloads.append(entry["name"])
        self.download_states.append(
            (entry["name"], ICBoothState.get().applied_manifest_hash)
        )
        data = self.files[entry["name"]]
        Path(part_path).parent.mkdir(parents=True, exist_ok=True)
        Path(part_path).write_bytes(data)
        if on_bytes:
            on_bytes(len(data))

    def post_status(self, report):
        self._reach()
        self.reports.append(report)

    def post_progress(self, progress):
        self._reach()
        self.progress.append(progress)


class AgentRunTests(ICTempDirsMixin, TestCase):
    """The booth side, in its own media/projection directories, fed by FakeParent."""

    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.parent = self.capture_production()
        booth_dir = Path(tempfile.mkdtemp(prefix="ic-booth-", dir=self._ic_tmp))
        self.booth_projection = booth_dir / "projection"
        self.booth_media = booth_dir / "media"
        self.booth_projection.mkdir()
        self.booth_media.mkdir()
        booth = override_settings(
            IC_PROJECTION_ROOT=self.booth_projection, MEDIA_ROOT=self.booth_media
        )
        booth.enable()
        self.addCleanup(booth.disable)

    def production(self):
        """Production's file locations, for making or reading production's files."""
        return override_settings(
            IC_PROJECTION_ROOT=self.projection_root, MEDIA_ROOT=self.media_root
        )

    def capture_production(self):
        """A FakeParent serving production's current bundle and file bytes."""
        with self.production():
            manifest, fixture, digest = build_bundle()
            files = {
                entry["name"]: Path(agent._storage_path(entry)).read_bytes()
                for entry in manifest["files"]
            }
        return FakeParent(manifest, fixture, digest, files)

    def run_sync(
        self, trigger=ICSyncRun.Trigger.FORCE_PULL, request_id=None, **options
    ):
        run = ICSyncRun.objects.create(
            trigger=trigger, options=options, request_id=request_id
        )
        return agent.execute_run(run, client=self.parent, heartbeat=lambda: None)

    def test_forced_run_downloads_verifies_and_applies(self):
        run = self.run_sync()
        self.assertEqual(
            run.status, ICSyncRun.Status.SUCCEEDED, run.error or run.report
        )
        self.assertEqual(len(self.parent.downloads), 3)
        for entry in self.parent.manifest_data["files"]:
            path = agent._storage_path(entry)
            self.assertTrue(
                path.is_relative_to(self.booth_projection)
                or path.is_relative_to(self.booth_media)
            )
            self.assertTrue(path.exists())
        self.assertEqual(ICLocalFile.objects.count(), 3)
        state = ICBoothState.get()
        self.assertEqual(state.applied_manifest_hash, self.parent.digest)
        self.assertIsNotNone(state.last_success_at)
        report = self.parent.reports[-1]
        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(report["applied_manifest_hash"], self.parent.digest)
        verified = {f["resource_file_id"]: f for f in report["verified_files"]}
        master = self.scenario["film_item"].projection_file()
        self.assertTrue(verified[master.pk]["hashed_this_run"])
        self.assertEqual(verified[master.pk]["xxh64"], master.checksum)
        self.assertTrue(all(p["ready"] for p in report["programs"]))

    def test_poll_with_unchanged_manifest_is_up_to_date(self):
        self.run_sync()
        downloads = len(self.parent.downloads)
        run = self.run_sync(trigger=ICSyncRun.Trigger.POLL)
        self.assertEqual(run.status, ICSyncRun.Status.UP_TO_DATE)
        self.assertEqual(len(self.parent.downloads), downloads)

    def test_forced_run_with_nothing_changed_still_reimports_and_verifies(self):
        self.run_sync()
        with mock.patch.object(
            agent, "apply_fixture", wraps=agent.apply_fixture
        ) as applied:
            run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED)
        applied.assert_called_once()
        self.assertEqual(
            len(self.parent.downloads), 3
        )  # trusted files are not fetched again
        report = self.parent.reports[-1]
        self.assertFalse(any(f["hashed_this_run"] for f in report["verified_files"]))

    def test_event_run_skips_the_import_when_the_fixture_is_unchanged(self):
        self.run_sync()
        self.parent.digest = "changed-files-only"
        with mock.patch.object(agent, "apply_fixture") as applied:
            run = self.run_sync(trigger=ICSyncRun.Trigger.EVENT)
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED, run.error)
        applied.assert_not_called()

    def test_full_rehash_detects_and_repairs_a_damaged_file(self):
        self.run_sync()
        entry = next(
            e for e in self.parent.manifest_data["files"] if e["kind"] == "projection"
        )
        path = agent._storage_path(entry)
        data = bytearray(path.read_bytes())
        data[10] ^= 0xFF
        path.write_bytes(bytes(data))
        run = self.run_sync(full_rehash=True)
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED, run.error)
        self.assertEqual(self.parent.downloads.count(entry["name"]), 2)
        self.assertEqual(path.read_bytes(), self.parent.files[entry["name"]])

    def test_corrupt_download_fails_without_changing_anything(self):
        entry = next(
            e for e in self.parent.manifest_data["files"] if e["kind"] == "projection"
        )
        self.parent.files[entry["name"]] = b"corrupt"
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.PARTIAL)
        self.assertFalse(agent._storage_path(entry).exists())
        program = run.report["programs"][0]
        self.assertFalse(program["ready"])
        self.assertTrue(any("Checksum failed" in p for p in program["problems"]))
        # The program is showing today, so its rows wait for its files.
        self.assertEqual(run.report["database"], "kept_previous")
        self.assertIn("kept its previous data", run.report["warnings"][0])
        self.assertEqual(ICBoothState.get().applied_manifest_hash, "")

    def test_a_partial_run_is_retried_by_the_next_poll(self):
        entry = next(
            e for e in self.parent.manifest_data["files"] if e["kind"] == "projection"
        )
        good = self.parent.files[entry["name"]]
        self.parent.files[entry["name"]] = b"corrupt"
        self.assertEqual(self.run_sync().status, ICSyncRun.Status.PARTIAL)
        self.parent.files[entry["name"]] = good
        run = self.run_sync(trigger=ICSyncRun.Trigger.POLL)
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED, run.error)
        self.assertEqual(ICBoothState.get().applied_manifest_hash, self.parent.digest)
        # Now that it succeeded, polls are cheap again.
        run = self.run_sync(trigger=ICSyncRun.Trigger.POLL)
        self.assertEqual(run.status, ICSyncRun.Status.UP_TO_DATE)

    def test_rows_apply_before_films_for_later_programs_download(self):
        """A fix for tonight must not wait behind next month's master."""
        later = timezone.localdate() + timedelta(days=20)
        program = ICProgram.objects.create(
            name="Next month", start_date=later, end_date=later
        )
        with self.production():
            upcoming = make_film(
                self.scenario["editor"], self.scenario["playlist"], "Next month's film"
            )
        ICProgramItem.objects.create(program=program, content=upcoming, position=0)
        self.parent = self.capture_production()

        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED, run.error)
        upcoming_master = ResourceFile.objects.get(
            resource=upcoming.resource, is_projection_file=True
        )
        applied_when = dict(self.parent.download_states)
        self.assertEqual(applied_when[upcoming_master.file.name], self.parent.digest)
        showing_master = self.scenario["film_item"].projection_file()
        self.assertEqual(applied_when[showing_master.file.name], "")

    def test_a_failed_download_for_a_later_program_still_applies_the_rows(self):
        later = timezone.localdate() + timedelta(days=20)
        program = ICProgram.objects.create(
            name="Next month", start_date=later, end_date=later
        )
        with self.production():
            upcoming = make_film(
                self.scenario["editor"], self.scenario["playlist"], "Next month's film"
            )
        ICProgramItem.objects.create(program=program, content=upcoming, position=0)
        self.parent = self.capture_production()
        upcoming_master = ResourceFile.objects.get(
            resource=upcoming.resource, is_projection_file=True
        )
        self.parent.files[upcoming_master.file.name] = b"corrupt"
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.PARTIAL)
        self.assertEqual(run.report["database"], "applied")
        ready = {p["name"]: p["ready"] for p in run.report["programs"]}
        self.assertEqual(ready, {"Amélie week": True, "Next month": False})

    def test_a_pushed_run_reports_progress_to_production(self):
        request_id = uuid.uuid4()
        run = self.run_sync(trigger=ICSyncRun.Trigger.FORCE_PUSH, request_id=request_id)
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED, run.error)
        self.assertTrue(self.parent.progress)
        self.assertTrue(
            all(p["request_id"] == str(request_id) for p in self.parent.progress)
        )
        self.assertIn("downloading", {p["phase"] for p in self.parent.progress})
        self.assertEqual(self.parent.reports[-1]["request_id"], str(request_id))

    def test_runs_nobody_pushed_post_no_progress(self):
        self.run_sync()
        self.assertEqual(self.parent.progress, [])

    def test_code_mismatch_refuses_to_import(self):
        self.parent.manifest_data["git_sha"] = "0" * 40
        with (
            mock.patch.object(agent, "git_sha", return_value="1" * 40),
            mock.patch.object(agent, "apply_fixture") as applied,
        ):
            run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.FAILED)
        self.assertEqual(run.report["code"], "code_mismatch")
        applied.assert_not_called()

    def test_migration_mismatch_says_which_side_is_behind(self):
        self.parent.manifest_data["migrations"] = {
            **self.parent.manifest_data["migrations"],
            "ic": "9999_future",
        }
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.FAILED)
        self.assertIn("booth is behind", run.error)

    def test_fingerprint_mismatch_keeps_the_previous_mirror(self):
        self.run_sync()
        state_before = ICBoothState.get().applied_fixture_sha256
        feature = self.scenario["feature"]
        self.parent.manifest_data["fingerprints"][str(feature.pk)] = "0" * 64
        self.parent.digest = "tampered"
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.FAILED)
        self.assertEqual(run.report["code"], "fingerprint_mismatch")
        self.assertEqual(ICBoothState.get().applied_fixture_sha256, state_before)
        self.assertTrue(Content.objects.filter(pk=feature.pk).exists())

    def test_unreachable_production_fails_cleanly(self):
        self.parent.unreachable = True
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.FAILED)
        self.assertIn("unreachable", run.error)
        self.assertEqual(ICBoothState.get().last_run_status, "failed")

    def test_not_enough_disk_space(self):
        with mock.patch.object(
            agent.shutil, "disk_usage", return_value=mock.Mock(free=10, total=100)
        ):
            run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.FAILED)
        self.assertEqual(run.report["code"], "disk_full")

    def test_a_lost_released_master_is_reported(self):
        self.run_sync()
        entry = next(
            e for e in self.parent.manifest_data["files"] if e["kind"] == "projection"
        )
        entry["available_from_parent"] = False
        agent._storage_path(entry).unlink()
        self.parent.digest = "released"
        run = self.run_sync(trigger=ICSyncRun.Trigger.EVENT)
        self.assertEqual(run.status, ICSyncRun.Status.PARTIAL)
        self.assertEqual(
            run.report["lost_files"],
            [{"kind": "projection", "resource_file_id": entry["resource_file_id"]}],
        )
        self.assertNotIn(entry["name"], self.parent.downloads[3:])


class CheckParentTests(ICTempDirsMixin, TestCase):
    """The agent's frequent check: the booth calls production, never the reverse."""

    def setUp(self):
        super().setUp()
        self.parent = FakeParent({}, b"", "hash-1", {})

    def test_a_new_manifest_hash_queues_one_event_run(self):
        queued = agent.check_parent(self.parent)
        self.assertEqual([r.trigger for r in queued], [ICSyncRun.Trigger.EVENT])
        state = ICBoothState.get()
        self.assertEqual(state.parent_manifest_hash, "hash-1")
        self.assertIsNotNone(state.parent_checked_at)
        self.assertTrue(state.production_has_newer_changes)
        # Still queued: no second run.
        self.assertEqual(agent.check_parent(self.parent), [])
        # Tried and failed: the 15-minute poll retries, not every check.
        ICSyncRun.objects.update(status=ICSyncRun.Status.FAILED, manifest_hash="hash-1")
        self.assertEqual(agent.check_parent(self.parent), [])
        # Production moved on: try the new state.
        self.parent.digest = "hash-2"
        self.assertEqual(len(agent.check_parent(self.parent)), 1)

    def test_nothing_to_do_when_the_booth_matches(self):
        state = ICBoothState.get()
        state.applied_manifest_hash = "hash-1"
        state.save()
        self.assertEqual(agent.check_parent(self.parent), [])
        self.assertFalse(ICBoothState.get().production_has_newer_changes)

    def test_a_push_becomes_one_forced_run_and_production_hears_it_was_collected(self):
        state = ICBoothState.get()
        state.applied_manifest_hash = "hash-1"
        state.save()
        request_id = str(uuid.uuid4())
        self.parent.sync_requests = [
            {
                "request_id": request_id,
                "requested_by": "Ada",
                "options": {"full_rehash": True, "something_else": True},
            }
        ]
        (run,) = agent.check_parent(self.parent)
        self.assertEqual(run.trigger, ICSyncRun.Trigger.FORCE_PUSH)
        self.assertEqual(str(run.request_id), request_id)
        self.assertEqual(run.requested_by, "Ada")
        self.assertEqual(run.options, {"full_rehash": True})
        self.assertEqual(self.parent.progress[-1]["status"], "queued")

        # Offered again (production hadn't heard yet): same run, nothing new.
        self.assertEqual(agent.check_parent(self.parent), [])
        self.assertEqual(ICSyncRun.objects.count(), 1)

        # Finished, but its report was lost: the next offer resends it.
        run.status = ICSyncRun.Status.SUCCEEDED
        run.report = {"request_id": request_id, "status": "succeeded"}
        run.save()
        agent.check_parent(self.parent)
        self.assertEqual(self.parent.reports[-1]["status"], "succeeded")

    def test_unreachable_production_is_recorded_and_queues_nothing(self):
        self.parent.unreachable = True
        self.assertEqual(agent.check_parent(self.parent), [])
        self.assertIn("unreachable", ICBoothState.get().parent_check_error)
        self.parent.unreachable = False
        agent.check_parent(self.parent)
        self.assertEqual(ICBoothState.get().parent_check_error, "")


class PruneTests(ICTempDirsMixin, TestCase):
    def make_local(self, name, *, available=True, last_end=None, verified_days_ago=0):
        path = Path(ResourceFile().file.storage.path(name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return ICLocalFile.objects.create(
            kind=ICLocalFile.Kind.PROJECTION,
            ref_id=ICLocalFile.objects.count() + 1,
            name=name,
            size=1,
            xxh64="0",
            verified_at=timezone.now() - timedelta(days=verified_days_ago),
            available_from_parent=available,
            last_program_end=last_end,
        ), path

    def test_unlisted_files_go_after_the_grace_period(self):
        today = timezone.localdate()
        recent, recent_path = self.make_local(
            "ic_projection/1/a.mp4", last_end=today - timedelta(days=1)
        )
        old, old_path = self.make_local(
            "ic_projection/2/b.mp4", last_end=today - timedelta(days=10)
        )
        removed, _ = agent.prune({"files": []}, today=today)
        self.assertEqual(removed, ["ic_projection/2/b.mp4"])
        self.assertTrue(recent_path.exists())
        self.assertFalse(old_path.exists())

    def test_released_masters_are_kept_much_longer_and_warned_about(self):
        today = timezone.localdate()
        only_copy, path = self.make_local(
            "ic_projection/3/c.mp4",
            available=False,
            last_end=today - timedelta(days=10),
        )
        removed, _ = agent.prune({"files": []}, today=today)
        self.assertEqual(removed, [])
        self.assertTrue(path.exists())
        listed = {
            "files": [{"kind": "projection", "resource_file_id": only_copy.ref_id}]
        }
        _, warnings = agent.prune(listed, today=today + timedelta(days=15))
        self.assertEqual(len(warnings), 1)
        self.assertIn("only copy", warnings[0])
