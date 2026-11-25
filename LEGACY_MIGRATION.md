# Legacy Migration Guide

Legacy Y-Video data is moved into this app in one bulk import, using two
management commands.

## Bulk Import

`import_legacy_data` copies database rows only. Imported files and subtitles
have no media until you run `import_legacy_media`.

Run both commands on the host, not inside the app container (where the legacy
folder is not mounted, so don't use `deploy/manage.sh`). Run them as the deploy
user from the repo root, with the app stopped so nothing else writes to the
database at the same time. Stop the deploy timer too, or a new commit would
restart the app in the middle of the run, and wait for any deploy already in
progress to finish. The host needs `uv` installed.

### Getting the source snapshot

`import_legacy_data` reads a SQLite snapshot of the legacy PostgreSQL
database. To create it, copy `scripts/dump_legacy_to_sqlite_settings_template.py`
to `scripts/dump_legacy_to_sqlite_settings.py`, fill in read-only credentials
for the legacy database, and run `uv run scripts/dump_legacy_to_sqlite.py`. It
copies only the tables the import needs into
`var/legacy_migration/legacy_dump.sqlite3`. Take the snapshot right before the
import: anything added to the legacy app afterwards is not migrated. The import
writes its `.dept_map` and `.cleaned-map` reports next to the snapshot.

### Running the import

```bash
systemctl --user stop <app>-deploy.timer
systemctl --user is-active <app>-deploy.service   # wait until this prints "inactive"
systemctl --user stop <app>.service               # also stops <app>-media-worker.service, if present
export YVIDEO_DB_DIR="$PWD/data"   # the container's database; without this the host uses ./db.sqlite3

uv run scripts/dump_legacy_to_sqlite.py
SNAPSHOT=var/legacy_migration/legacy_dump.sqlite3
uv run python manage.py import_legacy_data --source "$SNAPSHOT" --yearterm <YYYYT>            # dry run
uv run python manage.py import_legacy_data --source "$SNAPSHOT" --yearterm <YYYYT> --commit
uv run python manage.py import_legacy_media            # dry run: shows link/skip per file
uv run python manage.py import_legacy_media --commit

systemctl --user start <app>.service <app>-deploy.timer   # plus <app>-media-worker.service if IC_MEDIA_WORKER=1
```

`import_legacy_media` only hardlinks legacy files into `MEDIA_ROOT`; it never
copies them. Requirements:

- `LEGACY_MIGRATION_MEDIA_ROOT` must be an existing local directory on the
  same filesystem *and mount* as `MEDIA_ROOT`.
  Check that `findmnt -T <path>` shows the same target for both. A bind mount
  counts as a different mount and breaks hardlinks; a symlink does not.
- The deploy user must be able to read *and* write the legacy files (for
  example, through group write permission). Most Linux systems enable
  `fs.protected_hardlinks`, which refuses links to files the user doesn't own
  unless it has both.
- Apache serves `media/` directly, and hardlinked files keep the legacy
  owner and permissions, so the legacy files must be readable by Apache's
  user.

The dry run makes a real test link for each file. Files that can't be linked
are reported and skipped. The dry run does not hash files, so duplicates (below) are
only detected with `--commit`.

- Each file and subtitle is committed on its own, so the command is safe to
  stop and rerun. Finished items are not touched again.
- Missing, unreadable, or unlinkable legacy files are reported and skipped.
  They stay pending, so a later run retries them.
- Each file is hashed before it is placed. If it has the same bytes as an
  existing file in the same resource, its contents are pointed at the existing
  file and the duplicate placeholder is deleted. A match in a *different*
  resource is reported and left pending for manual review.
- A hardlinked file is shared by both apps: editing it in place changes it for
  both, but deleting either path is safe.

### Keeping migration I/O from slowing other services

This app is stopped during the import, but the legacy Y-Video app and anything
else on the same disks are still running. Both commands lower their own CPU
priority (`nice 19`) and request idle I/O priority (`ionice -c3`).
`import_legacy_media` must read every file once to hash it, so it also limits
reads to 100 MB/s; change it with `--max-read-mbps N` (`0` means unlimited).
At 100 MB/s, 3.3 TB takes about 9 hours.

`ionice -c3` is ignored by the `none` I/O scheduler (common for NVMe and VM
disks) and honored by `mq-deadline` (kernel 5.14+) and `bfq`. Software RAID can
drop the priority for some reads. Check with
`cat /sys/block/<dev>/queue/scheduler` on the physical disks under the legacy
media (`lsblk -s <device>`). The read limit works everywhere.

On spinning disks, watch the run with `iostat -x 5 <disks>` (from `sysstat`).
If `%util` stays near 100% or `r_await` stays above about 20–30 ms, stop the
command and rerun it with a lower `--max-read-mbps`; if the disks look idle,
raise it.
