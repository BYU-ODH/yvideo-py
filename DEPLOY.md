# Deployment

Three environments run on a single server behind Apache reverse proxies:

| Environment | Subdomain | Host Port | Directory | Auto-deploy branch |
|---|---|---|---|---|
| dev | dev.yvideo.example.com | 8001 | `/srv/yvideo/dev/` | none (manual) |
| staging | staging.yvideo.example.com | 8002 | `/srv/yvideo/staging/` | `main` |
| prod | yvideo.example.com | 8003 | `/srv/yvideo/prod/` | `prod` |

Each environment is an independent git clone with its own configuration
files, database, and Docker container.

## Architecture

```txt
                        ┌─ dev.yvideo.example.com ─────────→ :8001 → yvideo-dev container
Apache (443, TLS) ─────┤─ staging.yvideo.example.com ──────→ :8002 → yvideo-staging container
                        ├─ yvideo.example.com ──────────────→ :8003 → yvideo-prod container
                        └─ auto-deploy.yvideo.example.com ─→ :8004 → auto-deploy container
```

Apache serves `/static/` and `/media/` directly from the host filesystem.
All other requests are proxied to the Docker container. See
`deploy/apache-vhost.conf` for an example configuration.

## Deployment files

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the app image (Python 3.13, system deps for SAML, uv, gunicorn) |
| `compose.yml` | Defines the `web` service with bind mounts for data, media, config |
| `.env_template` | Template for the per-environment `.env` (copy to `.env`) |
| `deploy/entrypoint.sh` | Container entrypoint: runs migrate, collectstatic, starts gunicorn |
| `deploy/deploy.sh` | Pulls latest code, rebuilds, and restarts the container |
| `deploy/auto-deploy/` | Webhook microservice that receives deploy requests from GitHub Actions |
| `deploy/apache-vhost.conf` | Example Apache reverse proxy config for all environments |
| `.github/workflows/deploy.yml` | GitHub Actions workflow that triggers auto-deploy for staging and prod |

## Initial server setup

Repeat for each environment (`dev`, `staging`, `prod`):

```bash
# 1. Clone the repo
git clone git@github.com:BYU-ODH/yvideo-py.git /srv/yvideo/prod
cd /srv/yvideo/prod
git checkout main  # or prod, or any branch for dev

# 2. Create .env from template
cp .env_template .env
# Edit .env: set COMPOSE_PROJECT_NAME and HOST_PORT

# 3. Create secret_settings.py
cp yvideo/secret_settings_template.py yvideo/secret_settings.py
# Edit secret_settings.py — at minimum set:
#   DEBUG = False  (for staging/prod)
#   ALLOWED_HOSTS = ["yvideo.example.com"]
#   SECRET_KEY = "<unique random value>"
#   SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
#   CSRF_TRUSTED_ORIGINS = ["https://yvideo.example.com"]
#   API_CLIENT_ID, API_CLIENT_SECRET, and all API_*_URL values

# 4. Set up SAML config
# Place your SAML SP/IdP settings and certificates in:
#   yvideo/saml_config/settings.json
#   yvideo/saml_config/advanced_settings.json
#   yvideo/saml_config/certs/sp.cert
#   yvideo/saml_config/certs/sp.key

# 5. Create persistent data directories
mkdir -p data media var

# 6. Build and start
docker compose build
docker compose up -d

# 7. (dev/staging only) Seed demo data
# Only needed once — the database persists across deploys via the data/ bind mount.
docker compose exec web uv run python manage.py seed_demo_data
```

## Auto-deploy service setup

A small webhook microservice in `deploy/auto-deploy/` receives deploy
requests from GitHub Actions. It runs as its own Docker container on
the server, with access to the Docker socket and the deployment
directories.

```bash
cd /srv/yvideo
git clone git@github.com:BYU-ODH/yvideo-py.git auto-deploy-repo
cd auto-deploy-repo/deploy/auto-deploy

# Create .env with a shared secret (also stored as DEPLOY_SECRET in GitHub repo secrets)
cp .env_template .env
# Edit .env: set DEPLOY_SECRET to a long random string

docker compose build
docker compose up -d
```

Required GitHub repo secret: `DEPLOY_SECRET` (must match the value in
the auto-deploy `.env` file).

## Deploying updates

### Staging and prod (automatic)

Pushes to `main` and `prod` trigger the GitHub Actions workflow in
`.github/workflows/deploy.yml`, which POSTs to
`https://auto-deploy.yvideo.example.com/deploy` with the shared secret.
The auto-deploy service then runs `deploy/deploy.sh` in the
corresponding environment directory.

### Dev (manual)

SSH into the server and deploy whatever branch you want:

```bash
cd /srv/yvideo/dev
git fetch origin
git checkout my-branch
bash deploy/deploy.sh
```

### What deploy.sh does

1. `git fetch origin`
2. `git reset --hard origin/<current branch>` -- deploy directories are
   automation-managed; local edits are overwritten
3. `docker compose build --pull` -- rebuilds the image with latest code and
   base image
4. `docker compose up -d` -- restarts the container (the entrypoint runs
   `migrate` and `collectstatic` automatically)
5. `docker image prune -f` -- cleans up old images

## Viewing logs

```bash
cd /srv/yvideo/prod
docker compose logs -f        # follow all logs
docker compose logs -f web    # follow just the web service

# Auto-deploy service logs
cd /srv/yvideo/auto-deploy-repo/deploy/auto-deploy
docker compose logs -f
```

## Key configuration notes

- **SQLite database**: Stored in `data/db.sqlite3` (with WAL/SHM journal
  files alongside it). The `data/` directory is bind-mounted so all three
  files persist across container rebuilds.
- **Static files**: `collectstatic` writes to `staticfiles/` via bind mount.
  Apache serves this directory directly at `/static/`.
- **Media files**: User uploads go to `media/`, also served directly by
  Apache at `/media/`.
- **SAML config**: Bind-mounted read-only into the container. Each
  environment needs its own SP entity ID, ACS URL, and certificates.
- **Gunicorn**: Runs with `--preload` (so the legacy dump scheduler starts
  once in the master process), 2 workers, and 2 threads per worker.
