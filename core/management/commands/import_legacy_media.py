from collections import Counter
import os
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from core.legacy_migration import LegacyMigrationService
from core.legacy_migration import LegacySourceMap
from core.legacy_migration import io_limits
from core.legacy_migration.file_index import compute_checksum
from core.legacy_migration.io_limits import copy_file
from core.legacy_migration.io_limits import lower_process_priority
from core.legacy_migration.remote_files import is_remote_legacy_path
from core.legacy_migration.remote_files import resolve_legacy_file_path
from core.models import Content
from core.models import ResourceFile
from core.models import Subtitle
from core.subtitle_uploads import build_subtitle_vtt


class SkipItem(Exception):
    pass


class Command(BaseCommand):
    help = (
        "Transfer media for files and subtitles left pending by import_legacy_data. "
        "Dry run unless --commit is supplied. Safe to stop and rerun."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Place files in MEDIA_ROOT, write subtitles, and update the database.",
        )
        parser.add_argument(
            "--max-read-mbps",
            type=float,
            default=150,
            help="Disk read limit in MB/s (0 = unlimited). Default: 150.",
        )
        parser.add_argument(
            "--allow-copy",
            action="store_true",
            help="Copy files that cannot be hardlinked instead of skipping them "
            "(doubles their disk use).",
        )

    def handle(self, *args, **options):
        lower_process_priority()
        previous_limit = io_limits.max_read_mbps
        io_limits.max_read_mbps = options["max_read_mbps"]
        try:
            self._run(options)
        finally:
            io_limits.max_read_mbps = previous_limit

    def _run(self, options):
        if is_remote_legacy_path(settings.LEGACY_MIGRATION_MEDIA_ROOT):
            raise CommandError(
                "LEGACY_MIGRATION_MEDIA_ROOT must be a local path on the same "
                "filesystem as MEDIA_ROOT."
            )
        self.commit = options["commit"]
        self.allow_copy = options["allow_copy"]
        self.service = LegacyMigrationService(require_catalog=False)
        self.stdout.write(
            "COMMIT: transferring legacy media."
            if self.commit
            else "DRY RUN: no files or database rows will change."
        )

        counts = Counter()
        pending_ids = list(
            LegacySourceMap.objects.filter(
                source_system="legacy",
                source_type__in=["file", "subtitle"],
                metadata__media_pending=True,
            )
            .order_by("source_type", "pk")
            .values_list("pk", flat=True)
        )
        for pk in pending_ids:
            mapping = LegacySourceMap.objects.get(pk=pk)
            label = f"{mapping.source_type} {mapping.source_id}"
            try:
                if mapping.source_type == "file":
                    outcome = self._file(mapping)
                else:
                    outcome = self._subtitle(mapping)
            except (SkipItem, OSError, ValidationError) as exc:
                counts["skipped"] += 1
                self.stderr.write(self.style.WARNING(f"{label}: skipped: {exc}"))
                continue
            counts[outcome] += 1
            self.stdout.write(f"{label}: {outcome}")

        summary = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
        self.stdout.write(f"Summary: {summary or 'nothing pending'}.")
        if counts["copy"] or counts["would copy"]:
            self.stdout.write(
                self.style.WARNING(
                    "Some files were copied instead of hardlinked, which doubles "
                    "their disk use."
                )
            )

    def _file(self, mapping):
        resource_file = (
            ResourceFile.objects.select_related("resource")
            .filter(pk=mapping.target_id)
            .first()
        )
        if resource_file is None:
            raise SkipItem(f"ResourceFile {mapping.target_id} no longer exists.")
        if resource_file.file:
            self._mark_done(mapping)
            return "already has media"

        filepath = mapping.metadata.get("source_row", {}).get("filepath")
        if not filepath:
            raise SkipItem("Legacy row has no filepath.")
        source = Path(resolve_legacy_file_path(filepath))
        method = self._check_source(source)
        if not self.commit:
            return f"would {method}"

        checksum = compute_checksum(source)
        existing = (
            ResourceFile.objects.filter(checksum=checksum)
            .exclude(pk=resource_file.pk)
            .first()
        )
        if existing and existing.resource_id != resource_file.resource_id:
            # Repointing would move the content to another resource and
            # detach it from its subtitles and annotation sets.
            raise SkipItem(
                f"Same bytes as ResourceFile {existing.pk} in another "
                "resource; needs manual review."
            )
        if existing:
            with transaction.atomic():
                Content.objects.filter(resource_file=resource_file).update(
                    resource_file=existing
                )
                resource_file.delete()
                mapping.target_id = existing.pk
                self._mark_done(mapping)
            return f"duplicate of ResourceFile {existing.pk}"

        relative_name, destination = self.service._storage_destination(
            source, resource_file.resource, resource_file.version
        )
        if method == "link":
            os.link(source, destination)
        else:
            copy_file(source, destination)
        with transaction.atomic():
            resource_file.file.name = relative_name
            resource_file.checksum = checksum
            resource_file.checksum_at = timezone.now()
            resource_file.save()
            self._mark_done(mapping)
        return method

    def _check_source(self, path):
        if not path.is_file():
            raise SkipItem(f"Missing legacy file {path}.")
        if not os.access(path, os.R_OK):
            raise SkipItem(f"Unreadable legacy file {path}.")
        # Same-device checks aren't enough: Linux's fs.protected_hardlinks
        # refuses links to files this user doesn't own or can't write, so
        # try a real one.
        probe = Path(settings.MEDIA_ROOT) / ".import_legacy_media-probe"
        probe.unlink(missing_ok=True)
        try:
            os.link(path, probe)
        except OSError as exc:
            if not self.allow_copy:
                raise SkipItem(
                    f"Cannot hardlink {path} into MEDIA_ROOT ({exc}); fix "
                    "ownership/filesystem or pass --allow-copy."
                ) from exc
            return "copy"
        probe.unlink()
        return "link"

    def _subtitle(self, mapping):
        subtitle = Subtitle.objects.filter(pk=mapping.target_id).first()
        if subtitle is None:
            raise SkipItem(f"Subtitle {mapping.target_id} no longer exists.")
        if subtitle.subtitles_file:
            self._mark_done(mapping)
            return "already has media"
        vtt = build_subtitle_vtt(mapping.metadata.get("source_row", {}).get("content"))
        if not self.commit:
            return "would write subtitle"
        with transaction.atomic():
            subtitle.subtitles_file.save(
                f"legacy-{mapping.source_id}.vtt",
                ContentFile(vtt.encode("utf-8")),
                save=True,
            )
            self._mark_done(mapping)
        return "subtitle"

    def _mark_done(self, mapping):
        if self.commit:
            mapping.metadata["media_pending"] = False
            mapping.save(update_fields=["target_id", "metadata", "updated_at"])
