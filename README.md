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
```

3. Set up pre-commit hooks:
```bash
uv run pre-commit install
```

4. Create secret_settings.py from secret_settings_template.py. Populate secret_settings.py with the correct values

5. Run database migrations:
```bash
uv run manage.py makemigrations core  # Needed to init new database
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
`core` migration files created during pre-pilot development. It does not touch
`demo_media/` or `yvideo/secret_settings.py`.

If you want a one-click local app login for that admin, enable `DEV_QUICK_LOGIN_ENABLED = True`
in `yvideo/secret_settings.py` and visit `/login/dev/quick/` on localhost. The route
returns 404 unless both `DEBUG` and `DEV_QUICK_LOGIN_ENABLED` are true, and it is only
usable from `localhost` or `127.0.0.1`.

### Running the Development Server

Start the Django development server:
```bash
uv run manage.py runserver
```

The application will be available at http://localhost:8000

### Development Tools

- **Pre-commit hooks**: Automatically run linting and formatting on commit
- **Ruff**: Fast Python linter and formatter
- **ESLint**: Javascript/CSS/JSON linter

To manually run pre-commit on all files (this is the command used in
Github Actions):
```bash
uv run pre-commit run --all-files
```

For local database-backed Django tests, use the dedicated test settings module as
the standard pre-pilot workflow:
```bash
uv run manage.py test --settings=yvideo.test_settings
```

Those settings deliberately bypass `core` migrations during test database creation.
That is temporary and intentional while the schema is still changing and migrations
are not yet part of the committed source of truth.

To upgrade dependency versions, use the following commands:

```console
uv sync --upgrade
uv sync --upgrade --dev

npm update
```

Check to make sure that the version numbers stored in pyproject.toml and package.json are exact, i.e. `==` instead of `>=`/`^`/etc.
