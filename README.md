# yvideo-py
Experimental repo to rewrite Y-video using Django and HTMX

## Development Setup

### Prerequisites
- [uv](https://docs.astral.sh/uv/) package manager

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd yvideo-py
```

2. Install dependencies including development tools:
```bash
uv sync --dev
npm ci  # eslint and dependencies
uv run python -m playwright install chromium
```

3. Set up pre-commit hooks:
```bash
uv run pre-commit install
```

4. Create secret_settings.py from secret_settings_template.py. Populate secret_settings.py with the correct values

5. Run database migrations:
```bash
uv run manage.py migrate
```

6. Seed deterministic development data and copy checked-in sample media files:
```bash
uv run manage.py seed_demo_data
```

The deterministic demo dataset includes a local admin with netid `devadmin` and password `devadmin`.
The seed command copies the checked-in sample mp4 files from `demo_media/` into `MEDIA_ROOT`.

If you want to wipe local state and rebuild from the current models, use:
```bash
bash scripts/dangerously_reset_local_state.sh --bootstrap
```

That removes the local SQLite database, generated media under `media/`, and any local
derived files in `media/`. It does not touch `demo_media/`, committed migrations,
or `yvideo/secret_settings.py`.

If you want a one-click local app login for that admin, enable `DEV_QUICK_LOGIN_ENABLED = True`
in `yvideo/secret_settings.py` and visit `/login/dev/quick/` on localhost. The route
returns 404 unless both `DEBUG` and `DEV_QUICK_LOGIN_ENABLED` are true, and it is only
usable from `localhost` or `127.0.0.1`.

### Deployment

See [DEPLOY.md](DEPLOY.md) for production deployment with Podman Quadlets and Apache.

### Running the Development Server

Start the Django development server:
```bash
uv run manage.py runserver
```

The application will be available at http://localhost:8000

### Legacy Migration Snapshot Setup

If you want to use the legacy migration workflow, the Django app reads from a local
SQLite snapshot instead of connecting directly to the legacy PostgreSQL database.

1. Copy `scripts/dump_legacy_to_sqlite_settings_template.py` to `scripts/dump_legacy_to_sqlite_settings.py`
2. Fill in the legacy PostgreSQL credentials in `scripts/dump_legacy_to_sqlite_settings.py`
3. Test the script:

```bash
uv run scripts/dump_legacy_to_sqlite.py
```

4. Enable legacy migration in `yvideo/secret_settings.py`:

```python
LEGACY_MIGRATION_ENABLED = True
```

By default, the snapshot is written to
`var/legacy_migration/legacy_dump.sqlite3`. Every preflight run dumps a fresh
snapshot itself before reading it. See
[LEGACY_MIGRATION.md](LEGACY_MIGRATION.md) for details.

### Development Tools

- **Pre-commit hooks**: Automatically run linting and formatting on commit
- **Ruff**: Fast Python linter and formatter
- **ESLint**: Javascript/CSS/JSON linter

To manually run pre-commit on all files (this is the command used in
Github Actions):
```bash
uv run pre-commit run --all-files
```

For local database-backed Django tests, run:
```bash
uv run manage.py test
```

That uses the normal project settings and migration graph.

For browser-backed end-to-end tests against the deterministic demo dataset, run:
```bash
uv run pytest tests/e2e --browser chromium
```

The pytest Playwright e2e suite runs headless Chromium, enables the local dev quick-login
route for the test server, seeds demo data before each test, and runs in CI as a separate
job instead of inside pre-commit.

A few behaviours cannot be covered by either suite, because they depend on a platform the
test browsers do not have — iOS media handling, a screen reader, real display hardware.
See [MANUAL_TESTING.md](MANUAL_TESTING.md) for what those are, how to check them by hand,
and why each one resists automation. Check it before releasing anything that touches video
playback or blur annotations.

To upgrade dependency versions, use the following commands:

```console
uv sync --upgrade
uv sync --upgrade --dev

npm update
```

Check to make sure that the version numbers stored in pyproject.toml and package.json are exact, i.e. `==` instead of `>=`/`^`/etc.
