"""Import Electron IC Player films (.icf + JSON, optionally the .ic/ video) into Y-video."""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.models import Content
from core.models import User
from ic.legacy import import_film


class Command(BaseCommand):
    help = (
        "Import legacy IC annotation JSON as annotation sets. The mapping CSV has columns "
        "'icf' (path, relative to DIRECTORY or absolute) and 'content_id' (a content in an "
        "International Cinema playlist)."
    )

    def add_arguments(self, parser):
        parser.add_argument("directory", type=Path)
        parser.add_argument("--mapping", type=Path, required=True)
        parser.add_argument(
            "--with-video",
            action="store_true",
            help=(
                "Also ingest each .ic/ video as the film's projection master. Recommended: "
                "it is exactly what the legacy annotations were timed against."
            ),
        )
        parser.add_argument(
            "--owner",
            help="Username of the annotation sets' owner (default: playlist owner).",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, directory, mapping, with_video, owner, dry_run, **options):
        owner_user = None
        if owner:
            owner_user = (
                User.objects.filter(username=owner).first()
                or User.objects.filter(netid=owner).first()
            )
            if owner_user is None:
                raise CommandError(f"No user {owner!r}.")
        with open(mapping, newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows or not {"icf", "content_id"} <= set(rows[0]):
            raise CommandError("The mapping CSV needs 'icf' and 'content_id' columns.")
        failures = 0
        for row in rows:
            icf = Path(row["icf"])
            icf = icf if icf.is_absolute() else directory / icf
            content = Content.objects.filter(
                pk=row["content_id"], playlist__is_ic=True
            ).first()
            if content is None:
                self.stderr.write(
                    f"{icf}: content {row['content_id']} is not in an IC playlist; skipped."
                )
                failures += 1
                continue
            try:
                result = import_film(
                    icf,
                    content,
                    owner=owner_user,
                    with_video=with_video,
                    dry_run=dry_run,
                )
            except (OSError, ValueError, KeyError) as exc:
                self.stderr.write(f"{icf}: {exc}")
                failures += 1
                continue
            summary = (
                ", ".join(
                    f"{count} {kind}" for kind, count in sorted(result.counts.items())
                )
                or "no annotations"
            )
            note = " (dry run)" if dry_run else ""
            video = "; master queued for ingest" if result.upload else ""
            self.stdout.write(f"{icf.name} → {content.title}: {summary}{video}{note}")
            for skipped in result.skipped:
                self.stderr.write(
                    f"  skipped unknown annotation type {skipped['type']!r}"
                )
        if failures:
            raise CommandError(f"{failures} film(s) could not be imported.")
