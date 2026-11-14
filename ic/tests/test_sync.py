"""Production → booth sync: bundle, import, verification, and the agent's run pipeline."""

import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from unittest import mock

from django.test import TestCase
from django.test import override_settings
from django.utils import timezone

from core.models import MuteAnnotation
from core.models import ResourceFile
import core.views
from ic.models import ICBoothState
from ic.models import ICLocalFile
from ic.models import ICProgram
from ic.models import ICProgramItem
from ic.models import ICSyncRun
from ic.sync import agent
from ic.sync import bundle as bundle_module
from ic.sync.bundle import build_bundle
from ic.sync.client import ParentClient
from ic.sync.client import ParentError
from ic.testing import ICTempDirsMixin
from ic.testing import make_film
from ic.testing import make_program_scenario


class BundleTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()

    def test_the_bundle_carries_what_the_booth_plays(self):
        film = self.scenario["film_item"]
        # Readiness the projectionist can't act on stays on production.
        film.timing_verified = False
        film.content.resource_file.derived_from = None
        film.content.resource_file.save()
        film.save()
        bundle, _ = build_bundle()
        self.assertEqual(bundle["format"], bundle_module.BUNDLE_FORMAT)
        self.assertEqual(
            bundle["player_data"][str(film.pk)], bundle_module.player_data(film)
        )
        self.assertEqual(len(bundle["player_data"][str(film.pk)]["subtitleTracks"]), 1)
        (program,) = bundle["programs"]
        entry = next(e for e in program["items"] if e["id"] == film.pk)
        master = film.projection_file()
        self.assertEqual(
            entry["master"],
            {"resource_file_id": master.pk, "xxh64": master.checksum},
        )
        self.assertEqual(entry["problems"], [])
        self.assertEqual(
            [f["resource_file_id"] for f in bundle["files"]],
            sorted(i.projection_file().pk for i in (film, self.scenario["loop_item"])),
        )

    def test_a_missing_subtitle_file_doesnt_stop_the_bundle(self):
        """Production's player renders an unreadable VTT as "", and so does the bundle."""
        film = self.scenario["film_item"]
        subtitle = film.content.default_subtitle_track
        Path(subtitle.subtitles_file.path).unlink()
        bundle, _ = build_bundle()
        self.assertEqual(
            bundle["player_data"][str(film.pk)]["subtitleTracks"][0]["vtt"], ""
        )

    def test_the_bundle_carries_no_personal_data(self):
        editor = self.scenario["editor"]
        editor.netid = "abc123"
        editor.email = "person@byu.edu"
        editor.save()
        text = json.dumps(build_bundle()[0], ensure_ascii=False)
        for value in ("abc123", "person@byu.edu", editor.username):
            self.assertNotIn(value, text)

    def test_manifest_hash_is_stable_and_tracks_annotation_edits(self):
        first = build_bundle()[1]
        self.assertEqual(first, build_bundle()[1])
        mute = MuteAnnotation.objects.filter(active=True).first()
        mute.edit(end_time=mute.end_time + 1)
        self.assertNotEqual(first, build_bundle()[1])

    def test_past_programs_are_not_synced(self):
        program = self.scenario["program"]
        program.start_date = program.end_date = timezone.localdate() - timedelta(days=5)
        program.save()
        manifest, _ = build_bundle()
        self.assertEqual(manifest["files"], [])


class FakeParent:
    """Stands in for production: serves a captured manifest and file bytes."""

    def __init__(self, manifest, digest, files):
        self.manifest_data = manifest
        self.digest = digest
        self.files = files
        self.reports = []
        self.downloads = []
        self.download_states = []
        self.unreachable = False

    def _reach(self):
        if self.unreachable:
            raise ParentError("Production unreachable: connection refused")

    def check(self):
        self._reach()
        return {"manifest_hash": self.digest}

    def manifest(self):
        self._reach()
        return {**copy.deepcopy(self.manifest_data), "manifest_hash": self.digest}

    def download(self, entry, part_path, *, on_bytes=None):
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
            manifest, digest = build_bundle()
            files = {
                entry["name"]: agent._storage_path(entry["name"]).read_bytes()
                for entry in manifest["files"]
            }
        return FakeParent(manifest, digest, files)

    def run_sync(self, full_rehash=False):
        run = ICSyncRun.objects.create(full_rehash=full_rehash)
        return agent.execute_run(run, client=self.parent, heartbeat=lambda: None)

    def test_a_run_downloads_verifies_and_applies(self):
        run = self.run_sync()
        self.assertEqual(
            run.status, ICSyncRun.Status.SUCCEEDED, run.error or run.report
        )
        self.assertEqual(len(self.parent.downloads), 2)
        for entry in self.parent.manifest_data["files"]:
            path = agent._storage_path(entry["name"])
            self.assertTrue(path.is_relative_to(self.booth_projection))
            self.assertTrue(path.exists())
        self.assertEqual(ICLocalFile.objects.count(), 2)
        state = ICBoothState.get()
        self.assertEqual(state.applied_manifest_hash, self.parent.digest)
        self.assertEqual(
            state.bundle["player_data"], self.parent.manifest_data["player_data"]
        )
        report = self.parent.reports[-1]
        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(report["applied_manifest_hash"], self.parent.digest)
        verified = {f["resource_file_id"]: f for f in report["verified_files"]}
        master = self.scenario["film_item"].projection_file()
        self.assertEqual(verified[master.pk]["xxh64"], master.checksum)
        self.assertEqual(report["failed_files"], [])

    def test_production_side_problems_dont_make_a_run_partial(self):
        """Only what a retry could fix makes a run partial (and so retried every 15 min)."""
        later = timezone.localdate() + timedelta(days=20)
        ICProgram.objects.create(
            name="Not planned yet", start_date=later, end_date=later
        )
        self.parent = self.capture_production()
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED, run.report)
        self.assertIsNone(agent.check_parent(self.parent))

    def test_a_run_with_nothing_changed_fetches_nothing(self):
        self.run_sync()
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED)
        # Verified files are trusted, not fetched again.
        self.assertEqual(len(self.parent.downloads), 2)

    def test_full_rehash_detects_and_repairs_a_damaged_file(self):
        self.run_sync()
        entry = self.parent.manifest_data["files"][0]
        path = agent._storage_path(entry["name"])
        data = bytearray(path.read_bytes())
        data[10] ^= 0xFF
        path.write_bytes(bytes(data))
        run = self.run_sync(full_rehash=True)
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED, run.error)
        self.assertEqual(self.parent.downloads.count(entry["name"]), 2)
        self.assertEqual(path.read_bytes(), self.parent.files[entry["name"]])

    def test_a_corrupt_download_changes_nothing_and_is_retried(self):
        entry = self.parent.manifest_data["files"][0]
        good = self.parent.files[entry["name"]]
        self.parent.files[entry["name"]] = b"corrupt"
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.PARTIAL)
        self.assertFalse(agent._storage_path(entry["name"]).exists())
        self.assertIn("Checksum failed", run.report["failed_files"][0]["error"])
        # The program is showing today, so its bundle waits for its master.
        self.assertFalse(run.report["applied"])
        self.assertIn("kept its previous data", run.report["warnings"][0])
        self.assertEqual(ICBoothState.get().applied_manifest_hash, "")

        self.parent.files[entry["name"]] = good
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED, run.error)
        self.assertEqual(ICBoothState.get().applied_manifest_hash, self.parent.digest)

    def test_the_bundle_applies_before_films_for_later_programs_download(self):
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
        upcoming_master = ResourceFile.objects.get(
            resource=upcoming.resource, is_projection_file=True
        )

        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED, run.error)
        applied_when = dict(self.parent.download_states)
        self.assertEqual(applied_when[upcoming_master.file.name], self.parent.digest)
        showing_master = self.scenario["film_item"].projection_file()
        self.assertEqual(applied_when[showing_master.file.name], "")

        # A failed download for the later program doesn't hold back the bundle.
        agent._storage_path(upcoming_master.file.name).unlink()
        self.parent.files[upcoming_master.file.name] = b"corrupt"
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.PARTIAL)
        self.assertTrue(run.report["applied"])
        self.assertEqual(
            [f["name"] for f in run.report["failed_files"]],
            [upcoming_master.file.name],
        )

    def test_a_newer_player_data_format_keeps_the_previous_bundle(self):
        self.run_sync()
        applied_before = ICBoothState.get().applied_manifest_hash
        self.parent.manifest_data["format"] = bundle_module.BUNDLE_FORMAT + 1
        self.parent.digest = "newer"
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.FAILED)
        self.assertEqual(run.report["code"], "format_mismatch")
        self.assertEqual(ICBoothState.get().applied_manifest_hash, applied_before)

    def test_unreachable_production_fails_cleanly(self):
        self.parent.unreachable = True
        run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.FAILED)
        self.assertIn("unreachable", run.error)

    def test_a_file_that_does_not_fit_fails_only_itself(self):
        with mock.patch.object(
            agent.shutil, "disk_usage", return_value=mock.Mock(free=10, total=100)
        ):
            run = self.run_sync()
        self.assertEqual(run.status, ICSyncRun.Status.PARTIAL)
        self.assertFalse(run.report["applied"])
        errors = [f["error"] for f in run.report["failed_files"]]
        self.assertTrue(any("Not enough disk space" in e for e in errors))

    def test_a_damaged_released_master_is_never_trusted_again(self):
        self.run_sync()
        entry = self.parent.manifest_data["files"][0]
        entry["available_from_parent"] = False
        path = agent._storage_path(entry["name"])
        data = bytearray(path.read_bytes())
        data[0] ^= 0xFF
        path.write_bytes(bytes(data))
        self.parent.digest = "released"
        run = self.run_sync(full_rehash=True)
        # Only a re-upload can fix a lost master, so retrying wouldn't help.
        self.assertEqual(run.status, ICSyncRun.Status.SUCCEEDED)
        self.assertIn("re-upload the master", run.report["failed_files"][0]["error"])
        # An ordinary run afterwards must not take the damaged bytes for verified.
        self.parent.digest = "released-again"
        run = self.run_sync()
        self.assertEqual(len(run.report["failed_files"]), 1)
        self.assertFalse(
            ICLocalFile.objects.filter(
                resource_file_id=entry["resource_file_id"]
            ).exists()
        )
        self.assertNotIn(
            entry["resource_file_id"],
            [f["resource_file_id"] for f in run.report["verified_files"]],
        )


class CheckParentTests(ICTempDirsMixin, TestCase):
    """The agent's frequent check, the one place that decides when a run is due."""

    def setUp(self):
        super().setUp()
        self.parent = FakeParent({}, "hash-1", {})

    def finish(self, run, status, manifest_hash, minutes_ago=0):
        run.status = status
        run.manifest_hash = manifest_hash
        run.finished_at = timezone.now() - timedelta(minutes=minutes_ago)
        run.save()

    def test_a_new_manifest_hash_queues_one_run(self):
        state = ICBoothState.get()
        state.applied_manifest_hash = "hash-1"
        state.save()
        self.assertIsNone(agent.check_parent(self.parent))
        self.parent.digest = "hash-2"
        run = agent.check_parent(self.parent)
        self.assertFalse(run.full_rehash)
        state = ICBoothState.get()
        self.assertEqual(state.parent_manifest_hash, "hash-2")
        self.assertTrue(state.production_has_newer_changes)
        # Still queued: no second run.
        self.assertIsNone(agent.check_parent(self.parent))

    def test_unclean_runs_are_retried_later_unless_production_changes(self):
        run = agent.check_parent(self.parent)
        self.finish(run, ICSyncRun.Status.FAILED, "hash-1")
        self.assertIsNone(agent.check_parent(self.parent))
        # Production moved on: try the new state at once.
        self.parent.digest = "hash-2"
        run = agent.check_parent(self.parent)
        self.assertIsNotNone(run)
        ICSyncRun.objects.exclude(pk=run.pk).delete()
        # A partial run is retried even when the hash matches, but not at once.
        state = ICBoothState.get()
        state.applied_manifest_hash = "hash-2"
        state.save()
        self.finish(run, ICSyncRun.Status.PARTIAL, "hash-2", minutes_ago=1)
        self.assertIsNone(agent.check_parent(self.parent))
        self.finish(run, ICSyncRun.Status.PARTIAL, "hash-2", minutes_ago=16)
        run = agent.check_parent(self.parent)
        self.assertIsNotNone(run)
        # A run that never saw a manifest waits like any other retry.
        self.finish(run, ICSyncRun.Status.FAILED, "")
        self.assertIsNone(agent.check_parent(self.parent))

    def test_weekly_rehash_of_only_copies_in_the_morning(self):
        state = ICBoothState.get()
        state.applied_manifest_hash = "hash-1"
        state.save()
        morning = timezone.localtime().replace(hour=4)
        evening = timezone.localtime().replace(hour=20)
        # Nothing released: nothing to re-hash.
        self.assertIsNone(agent.check_parent(self.parent, now=morning))
        ICLocalFile.objects.create(
            resource_file_id=1,
            name="ic/1/master.mp4",
            size=1,
            xxh64="0",
            verified_at=timezone.now(),
            available_from_parent=False,
        )
        self.assertIsNone(agent.check_parent(self.parent, now=evening))
        run = agent.check_parent(self.parent, now=morning)
        self.assertTrue(run.full_rehash)
        self.finish(run, ICSyncRun.Status.SUCCEEDED, "hash-1")
        self.assertIsNone(
            agent.check_parent(self.parent, now=morning + timedelta(days=1))
        )

    def test_unreachable_production_is_recorded_and_queues_nothing(self):
        self.parent.unreachable = True
        self.assertIsNone(agent.check_parent(self.parent))
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
            resource_file_id=ICLocalFile.objects.count() + 1,
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
        listed = {"files": [{"resource_file_id": only_copy.resource_file_id}]}
        _, warnings = agent.prune(listed, today=today + timedelta(days=15))
        self.assertEqual(len(warnings), 1)
        self.assertIn("only copy", warnings[0])


class HashRateTests(TestCase):
    """The booth hashes from the disk the projector plays from, so hashing is capped."""

    def test_capped_hashing_sleeps_to_hold_the_rate_and_gives_the_same_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "master.mp4"
            path.write_bytes(b"x" * (3 * 1024 * 1024))
            uncapped = bundle_module.file_xxh64(path)
            progress = []
            with mock.patch.object(bundle_module.time, "sleep") as sleep:
                capped = bundle_module.file_xxh64(
                    path,
                    chunk_size=1024 * 1024,
                    max_bytes_per_second=1024 * 1024,
                    on_bytes=progress.append,
                )
            self.assertEqual(capped, uncapped)
            # Progress (and so the agent's heartbeat) moves while a long hash runs.
            self.assertEqual(progress, [1024 * 1024] * 3)
            # 3 MB at 1 MB/s: the reader waits roughly three seconds in total.
            self.assertGreater(sum(call.args[0] for call in sleep.call_args_list), 2.5)


class _TestClientSession:
    """A requests.Session stand-in that sends ParentClient's requests through Django's
    test client, so the real client meets the real sync API in one process."""

    def __init__(self, client):
        self.client = client
        self.headers = {}

    def request(self, method, url, headers=None, **kwargs):
        extra = {
            "HTTP_" + name.upper().replace("-", "_"): value
            for name, value in {**self.headers, **(headers or {})}.items()
        }
        path = url.removeprefix("http://production")
        if method == "POST":
            response = self.client.post(
                path, data=kwargs["json"], content_type="application/json", **extra
            )
        else:
            response = self.client.get(path, **extra)
        body = (
            b"".join(response.streaming_content)
            if response.streaming
            else response.content
        )
        return mock.Mock(
            status_code=response.status_code,
            content=body,
            json=lambda: json.loads(body),
            iter_content=lambda size: [
                body[i : i + size] for i in range(0, len(body), size)
            ],
        )


@override_settings(IC_SYNC_TOKEN="booth-token")
class ParentClientTests(ICTempDirsMixin, TestCase):
    """The booth's real HTTP client against production's real sync API."""

    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.client_ = self.parent_client("booth-token")

    def parent_client(self, token):
        return ParentClient(
            base_url="http://production",
            token=token,
            session=_TestClientSession(self.client),
        )

    def test_check_manifest_and_a_resumed_ranged_download(self):
        manifest = self.client_.manifest()
        self.assertEqual(
            self.client_.check()["manifest_hash"], manifest["manifest_hash"]
        )
        master = self.scenario["film_item"].projection_file()
        entry = next(
            e for e in manifest["files"] if e.get("resource_file_id") == master.pk
        )
        data = Path(master.file.path).read_bytes()
        part = self._ic_tmp / "download.part"
        part.write_bytes(data[:1000])  # an earlier, interrupted download
        received = []
        # Production answers each request with a slice; the client carries on from it.
        with mock.patch.object(core.views, "MAX_RANGE_RESPONSE_BYTES", 4096):
            self.client_.download(entry, part, on_bytes=received.append)
        self.assertEqual(part.read_bytes(), data)
        self.assertEqual(sum(received), len(data) - 1000)

        master.master_released_at = timezone.now()
        master.save()
        part.unlink()
        with self.assertRaisesMessage(ParentError, "no longer holds"):
            self.client_.download(entry, part)

    def test_a_wrong_token_is_reported(self):
        with self.assertRaisesMessage(ParentError, "rejected this booth's sync token"):
            self.parent_client("wrong").check()
