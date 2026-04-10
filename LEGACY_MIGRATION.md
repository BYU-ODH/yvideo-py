# Legacy Migration Guide

This document explains exactly how a site administrator should migrate a collection from the legacy Y-Video system into this Django application.

It is written for the current implementation in this repository.

## Overview

The migration workflow has three parts:

1. A migration request is created.
2. A preflight job reads the local legacy SQLite snapshot and legacy media filesystem and prepares a reviewable migration plan.
3. An administrator reviews the plan in Django admin, resolves any issues, approves it, and then runs the migration worker to perform the import.

The legacy PostgreSQL database is used only by the dump script as a source of data. The Django app reads the generated SQLite snapshot. Imported records are written into the new application's database and media storage.

## What Gets Migrated for a Collection

A collection migration can import:

- The collection itself
- Resources used by the collection
- Files for those resources
- Collection contents
- URL-only contents
- Subtitles
- Clips
- Annotations
- Course links
- Collection access
- Resource access

## Before You Start

Make sure the following are true.

### 1. The app database is migrated

Run:

```bash
uv run python manage.py migrate
```

This must include `core.0002_content_resource_and_more`, which creates the legacy migration tables.

You can verify with:

```bash
uv run python manage.py showmigrations core
```

You should see:

```text
[X] 0001_initial
[X] 0002_content_resource_and_more
```

### 2. Legacy migration is enabled

In `yvideo/secret_settings.py`, set:

```python
LEGACY_MIGRATION_ENABLED = True
```

If this is `False`, the UI link is hidden and `/legacy-migrations/` returns `404`.

### 3. Configure the legacy dump script

The Django app reads a local SQLite snapshot for legacy migration. That snapshot is produced by `scripts/dump_legacy_to_sqlite.py`.

Create the script settings file:

1. Copy `scripts/dump_legacy_to_sqlite_settings_template.py` to `scripts/dump_legacy_to_sqlite_settings.py`
2. Fill in `SOURCE_DATABASE` with the legacy PostgreSQL credentials
3. Keep those credentials read-only

Example:

```python
SOURCE_DATABASE = {
    "dbname": "...",
    "user": "...",
    "password": "...",
    "host": "...",
    "port": 5432,
    "sslmode": "prefer",
}
```

By default, the SQLite snapshot is written to:

```text
var/legacy_migration/legacy_dump.sqlite3
```

### 4. The legacy media root is mounted on the same host

In `yvideo/secret_settings.py`, configure:

```python
LEGACY_MIGRATION_MEDIA_ROOT = "/absolute/path/to/legacy/media/root"
```

This should be the root directory that contains the legacy files referenced by the old `files.filepath` values.

The migration code expects direct local filesystem access. It does not use SSH.

### 5. Create the initial SQLite snapshot

Run:

```bash
uv run scripts/dump_legacy_to_sqlite.py
```

Do this once before using preflight for the first time.

### 6. Optional: allow automatic creation of missing users

If you want the app to try to create missing users from BYU IDs during preflight, set:

```python
LEGACY_MIGRATION_CREATE_MISSING_USERS = True
```

If this is `False`, unresolved users must be mapped manually in Django admin before approval.

## Automatic Daily Snapshot Refresh

While the Django server is running, it launches the dump script as a subprocess every day at 3:00 AM local server time:

```bash
uv run scripts/dump_legacy_to_sqlite.py
```

If you want to disable that scheduler, set:

```python
LEGACY_MIGRATION_AUTO_DUMP_ENABLED = False
```

## Starting the Worker

Queued jobs do not run by themselves. A worker must be running.

For one job at a time:

```bash
uv run python manage.py process_legacy_migration_jobs --once
```

For continuous processing:

```bash
uv run python manage.py process_legacy_migration_jobs
```

In production, run the loop mode under a service manager such as `systemd`.

## How a Collection Migration Is Requested

There are two ways to start.

### Option A: Instructor submits the request

1. The instructor signs in.
2. The instructor goes to `Manage Collections`.
3. The instructor clicks `Migrate from legacy Y-VIDEO`.
4. The instructor submits:
   - `migration_kind = collection`
   - `legacy_reference =` a full legacy collection URL or a raw legacy UUID
   - optional request notes

After submission, the app creates a queued preflight job.

### Option B: Administrator creates the request manually

1. Open Django admin at `/admin/`.
2. Open `Legacy Migration Requests`.
3. Click `Add Legacy migration request`.
4. Fill in:
   - `requested_by`
   - `target_owner`
   - `migration_kind = collection`
   - `legacy_reference =` the legacy collection URL or UUID
   - optional notes
5. Save.

Then run preflight from the admin action list as described below.

## Step-by-Step Admin Workflow

This is the exact operational workflow for approving and starting a collection migration.

### Step 1. Open the request in Django admin

Go to:

- `/admin/`
- `Legacy Migration Requests`

You will see the request record and its current status.

Common statuses:

- `submitted`: request exists and is waiting for preflight
- `needs_review`: preflight finished and needs admin review
- `approved`: approved for import
- `queued`: waiting for the worker
- `running`: worker is processing it
- `completed`: import finished
- `failed`: import failed
- `preflight_failed`: preflight failed
- `canceled`: manually canceled

### Step 2. Run preflight

From the Django admin list page for `Legacy Migration Requests`:

1. Select the request.
2. Choose `Run preflight now`.
3. Submit the action.

Preflight reads the SQLite snapshot and the legacy filesystem, then creates:

- `LegacyMigrationResource` rows
- `LegacyMigrationFileDecision` rows
- `LegacyMigrationUserResolution` rows
- `LegacyMigrationIssue` rows

After preflight, the request should move to `needs_review`.

### Step 3. Review the preflight results

Open the request detail page in Django admin and review the inlines.

#### A. `LegacyMigrationResource`

This shows each resource the collection depends on.

Review:

- `target_resource_name`
- `include`
- fuzzy-match warnings about similar current resources

Use `include = False` if a resource should not be migrated.

#### B. `LegacyMigrationFileDecision`

This is the most important review area for file migration.

Each row shows:

- the legacy resource
- the legacy version
- file size
- modified time
- last accessed time, if available
- device/inode
- absolute path
- linked contents
- linked collections
- linked instructors/TAs
- candidate matching current files

For each file, choose one action:

- `import`
- `reuse existing`
- `skip`

If you choose `reuse existing`, you must also set `selected_existing_resource_file`.

Use this section to avoid unnecessary duplicate imports and to decide whether an existing exact-match file should be reused.

#### C. `LegacyMigrationUserResolution`

This shows legacy users involved in the collection.

Review:

- collection owner
- collection access users
- resource access users

If a user was not auto-mapped:

1. Set `resolution_status = manual`
2. Set `matched_user` to the correct current user
3. Save

If the user should not be migrated:

1. Set `resolution_status = skip`
2. Optionally add notes

#### D. `LegacyMigrationIssue`

This shows warnings and blocking issues.

Common blocking issues include:

- unresolved users
- missing legacy files
- duplicate exact-match files that require a decision
- missing subtitle language mappings
- conflicting reuse choices
- similar resource names that require review

Warnings do not block approval. Blocking issues do.

### Step 4. Save your edits and refresh issues

After editing file decisions or user resolutions:

1. Save the request page.
2. Go back to the `Legacy Migration Requests` list.
3. Select the request.
4. Run `Refresh issues after user/file edits`.

This recomputes the blocking issues using your latest decisions.

Do not approve the request until all blocking issues are gone.

### Step 5. Approve and queue the import

Once there are no blocking issues:

1. Select the request in the `Legacy Migration Requests` admin list.
2. Run `Approve and queue import`.

This changes the request to `approved`, creates an import job, and then moves the request to `queued`.

At this point the request is approved, but the actual import still has not run until the worker processes the queued job.

### Step 6. Start the import worker

Run either:

```bash
uv run python manage.py process_legacy_migration_jobs --once
```

or:

```bash
uv run python manage.py process_legacy_migration_jobs
```

The worker processes queued preflight and import jobs in creation order.

### Step 7. Watch the request until it finishes

Open the request in Django admin and monitor:

- `status`
- `LegacyMigrationJob`
- `current_phase`
- `last_error`
- `Imported Targets`

During import, the worker records phases such as:

- `users`
- `courses`
- `resources`
- `files`
- `contents`
- `subtitles`
- `annotations`
- `permissions`
- `finalize`

When it completes successfully, the request status becomes `completed`.

The `Imported Targets` section links the legacy source IDs to the newly created Django objects.

## What the Import Does with Ownership and Permissions

For collection migrations:

- The request's `target_owner` becomes the owner of the new collection.
- The resolved legacy collection owner is also given instructor access if they are different from the target owner.
- Resolved legacy collection access rows are imported into `CollectionUserAccess`.
- Resolved legacy resource access rows are imported into `ResourceAccess`.
- The target owner is always given access to imported resources.

## How File Import Works

When a file is imported:

1. The system checks for exact existing matches by:
   - same real path
   - same device/inode
   - same checksum
2. If an exact match already exists, the admin must decide whether to reuse it, skip it, or change the plan.
3. If importing a new file and the legacy media root is on the same filesystem device as `MEDIA_ROOT`, the app hard-links the file instead of copying it.
4. If hard-linking is not possible, the app falls back to a local copy.

This is why the file review step is important.

## Verifying the Imported Collection

After completion, verify the new collection in the application.

Check:

1. The collection exists and is owned by the intended target owner.
2. The expected contents appear in the collection.
3. Imported video/audio files play.
4. URL-only contents open correctly.
5. Subtitles are present.
6. Annotations and clips are present where expected.
7. Courses are linked.
8. Instructors/TAs have the intended access.

## If Something Fails

If preflight fails:

- Open the request
- Read `latest_job_error`
- Fix configuration or data problems
- Run `Run preflight now` again

If import fails:

- Open the request
- Read `latest_job_error`
- Inspect the latest `LegacyMigrationJob`
- Fix the blocking problem
- Use `Retry latest failed job`

If a queued or running job should be stopped:

- Select the request in admin
- Run `Cancel queued/running jobs`

## Common Problems

### The legacy migration page returns `404`

`LEGACY_MIGRATION_ENABLED` is probably `False`.

### The legacy migration page says the tables are not installed

Run:

```bash
uv run python manage.py migrate
```

### Preflight says the legacy file is missing

`LEGACY_MIGRATION_MEDIA_ROOT` is wrong, the file is missing on disk, or the app user cannot read it.

### Preflight says the legacy SQLite dump does not exist yet

Run:

```bash
uv run scripts/dump_legacy_to_sqlite.py
```

### Preflight says a subtitle language is missing

The new database does not have a matching `Language` row for the legacy subtitle language code.

### Import does not start after approval

The request is only queued. Start the worker:

```bash
uv run python manage.py process_legacy_migration_jobs --once
```

### A user could not be resolved

Map them manually in `LegacyMigrationUserResolution`, then run `Refresh issues after user/file edits`.

## Recommended Production Setup

- Use read-only credentials in `scripts/dump_legacy_to_sqlite_settings.py`.
- Mount the legacy media directory read-only.
- Run `process_legacy_migration_jobs` under a service manager.
- Restrict migration approval to trusted admins.
- Test the workflow on a non-production copy of the legacy data before first real use.
