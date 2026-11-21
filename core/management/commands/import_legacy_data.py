from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db.models import Q

from core.legacy_migration.bulk_import import BulkImportError
from core.legacy_migration.bulk_import import LegacyBulkImporter
from core.legacy_migration.io_limits import lower_process_priority
from core.models import User


class Command(BaseCommand):
    help = (
        "Import the supported old_db tables from a PostgreSQL .sql dump (or SQLite snapshot); no media I/O. "
        "Dry run unless --commit is supplied. A .dept_map report is written even during dry runs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=True,
            help="Plain-text PostgreSQL .sql dump using COPY, or a legacy SQLite snapshot.",
        )
        parser.add_argument(
            "--yearterm",
            required=True,
            help="Year/term for legacy courses, e.g. 20265 (Fall 2026).",
        )

        parser.add_argument(
            "--user-map",
            help="Optional JSON object mapping legacy user UUIDs to existing target usernames.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist database rows (no media transfer).",
        )

    def handle(self, *args, **options):
        lower_process_priority()
        self.stdout.write(
            "COMMIT: importing into the configured default database."
            if options["commit"]
            else "DRY RUN: database changes will roll back; no media will be written."
        )
        importer = None
        try:
            importer = LegacyBulkImporter(
                source=options["source"],
                yearterm=options["yearterm"],
                user_map=options["user_map"],
                commit=options["commit"],
            )
            counts = importer.run()
        except BulkImportError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if importer is not None and importer.dept_map_written:
                self.stdout.write(f"Department map written: {importer.dept_map_path}")
        for table, totals in counts.items():
            self.stdout.write(
                f"{table}: {totals['created']} created, {totals['reused']} reused, "
                f"{totals['deleted']} soft-deleted excluded, {totals['skipped']} filtered out"
            )
        self.stdout.write(
            "New source mappings retain legacy-only fields in metadata, not as app behavior. "
            "Users with account_type=3 (students), an invalid BYU Person ID, or no netid are filtered out, "
            "along with their access rows, unless --user-map names them. "
            "Their owned collections and collection-specific data are also filtered out. "
            "Legacy global privileges are NOT imported. "
            "New resource IMDb IDs, resource-file barcodes, and checksums are left NULL. "
            "Media fields are empty; legacy file paths and subtitle content are retained in source mappings."
        )
        self._warn_users_without_netid()
        self.stdout.write(
            self.style.SUCCESS(
                "Import committed. Run import_legacy_media to transfer files and subtitles."
                if options["commit"]
                else "Dry run passed; all database changes rolled back."
            )
        )

    def _warn_users_without_netid(self):
        # The import matches existing users by netid. A user with no netid gets
        # a second, imported account, and the next import fails once they log in.
        usernames = list(
            User.objects.filter(Q(netid__isnull=True) | Q(netid=""))
            .order_by("username")
            .values_list("username", flat=True)
        )
        if usernames:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(usernames)} existing user(s) have no netid, so the import "
                    "can't match them to a legacy user. If any of them is a legacy "
                    "user, map them with --user-map: " + ", ".join(usernames)
                )
            )
