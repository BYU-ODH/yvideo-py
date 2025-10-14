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

To upgrade dependency versions, use the following commands:

```console
uv sync --upgrade
uv sync --upgrade --dev

npm update
```

Check to make sure that the version numbers stored in pyproject.toml and package.json are exact, i.e. `==` instead of `>=`/`^`/etc.
