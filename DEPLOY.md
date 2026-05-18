# Deployment

Each server runs a single instance of the application behind an Apache
reverse proxy. The application runs as a dedicated Unix user in a Podman
container, with data and static files bind-mounted from the host.

## Architecture

```txt
Apache (443, TLS) ──→ 127.0.0.1:8000 → yvideo.service (user yvideo)
```

Apache serves `/static/` and `/media/` directly from the host filesystem.
All other requests are proxied to the Podman container. See
`deploy/apache-vhost-example.conf` for an example configuration.

## Deployment files

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the app image for Podman (Python 3.13, system deps for SAML, uv, gunicorn) |
| `.containerignore` | Explicit build-context ignore file used by the Quadlet build unit |
| `.env_template` | Template for the `.env` file used by deploy scripts to configure the Unix user, port, and Gunicorn tuning |
| `deploy/quadlet.build.in` | Template for the Quadlet build unit |
| `deploy/quadlet.container.in` | Template for the Quadlet container unit |
| `deploy/common.sh` | Shared helpers for loading `.env`, validating values, and locating Quadlet paths |
| `deploy/install_quadlet.sh` | Renders and installs Quadlet units into the user Quadlet directory |
| `deploy/manage.sh` | Runs Django management commands inside the running container |
| `deploy/entrypoint.sh` | Container entrypoint: runs migrate, collectstatic, starts gunicorn |
| `deploy/deploy.sh` | Verifies the expected branch, hard-resets to origin, refreshes Quadlets, and restarts the service |
| `deploy/apache-vhost-example.conf` | Example Apache reverse proxy config |

## Initial server setup

### 1. Host prerequisites

Install Podman and create a dedicated Unix user for the application:

```bash
sudo useradd \
  --create-home \
  --home-dir /srv/yvideo \
  --shell /bin/bash \
  yvideo

sudo loginctl enable-linger yvideo
```

`enable-linger` keeps `systemd --user` running after the SSH session ends.
The dedicated Unix user isolates the application's Podman storage and Quadlet
units from other system services.

### 2. Clone the repo

```bash
sudo -iu yvideo
git clone git@github.com:BYU-ODH/yvideo-py.git /srv/yvideo/app
cd /srv/yvideo/app
git checkout main
```

### 3. Create `.env`

```bash
cp .env_template .env
```

Edit `.env` and set at least:

- `DEPLOY_USER=yvideo`
- `APP_NAME=yvideo`
- `HOST_PORT=8000`
- `WORKERS=2`
- `THREADS=2`

The deployment scripts enforce `DEPLOY_USER` at runtime. If someone runs
`deploy.sh`, `install_quadlet.sh`, or `manage.sh` from the wrong Unix
user, the script exits instead of touching the wrong instance.

`APP_NAME` becomes all of the following:

- The systemd user service name: `yvideo.service`
- The Podman container name: `yvideo`
- The local image tag: `localhost/yvideo:latest`

### 4. Create `secret_settings.py`

```bash
cp yvideo/secret_settings_template.py yvideo/secret_settings.py
```

Edit `secret_settings.py`. At minimum set:

- `DEBUG = False`
- `ALLOWED_HOSTS = ["example.com"]` (use your actual domain)
- `SECRET_KEY = "<unique random value>"`
- `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`
- `CSRF_TRUSTED_ORIGINS = ["https://example.com"]`
- `API_CLIENT_ID`, `API_CLIENT_SECRET`, and all `API_*_URL` values

### 5. Set up SAML config

Place your SAML SP/IdP settings and certificates in:

- `yvideo/saml_config/settings.json`
- `yvideo/saml_config/advanced_settings.json`
- `yvideo/saml_config/certs/sp.cert`
- `yvideo/saml_config/certs/sp.key`

### 6. Render and install the Quadlets

```bash
bash deploy/install_quadlet.sh
systemctl --user start yvideo-build.service
systemctl --user start yvideo.service
```

`install_quadlet.sh` creates or updates:

- `~/.config/containers/systemd/yvideo.build`
- `~/.config/containers/systemd/yvideo.container`

Start `yvideo-build.service` whenever you want to rebuild the local
image from the checkout. The build unit uses `Pull=always`, so deploy-time
rebuilds also pick up newer base-image layers.

### 7. Seed demo data when needed

Only do this once when initially setting up the instance. The SQLite database
lives on the host bind mount and persists across deploys.

```bash
bash deploy/manage.sh seed_demo_data
```

## Manually deploying updates

SSH into the server and run the deploy script in the environment you
want to update. For `dev`, you can switch to any branch first, but
`staging` and `prod` should always deploy from their respective branches.

```bash
sudo -iu yvideo
cd /srv/yvideo/app
git fetch origin
git checkout my-branch
bash deploy/deploy.sh my-branch
```

### What `deploy.sh` does

1. Verifies that the checked-out local branch matches the required `<branch>` argument and exits if it does not.
2. Runs `git fetch origin`.
3. Runs `git reset --hard origin/<branch>` so the checkout exactly matches the remote branch.
4. Renders and installs fresh Quadlet unit files from `.env`.
5. Runs `systemctl --user start yvideo-build.service`, which rebuilds the local image with `Pull=always` so base-image updates are picked up.
6. Runs `systemctl --user restart yvideo.service`.
7. Runs `podman image prune -f` to clean up unused images.

## Viewing logs and status

```bash
sudo -iu yvideo
cd /srv/yvideo/app
systemctl --user status yvideo.service
journalctl --user -u yvideo.service -f
journalctl --user -u yvideo-build.service -f
```

## Running management commands

```bash
sudo -iu yvideo
cd /srv/yvideo/app
bash deploy/manage.sh showmigrations  # list migration status (applied vs pending)
bash deploy/manage.sh shell           # open an interactive Django shell in the container
```

## Key configuration notes

- **Dedicated Unix user**: The application runs under a dedicated Unix user (e.g. `yvideo`) with its own home directory, Quadlet directory, and Podman storage. The scripts enforce this with `DEPLOY_USER` and a checkout ownership check.
- **Rootless services**: The deployment scripts refuse to run as `root`. All Quadlets install into `~/.config/containers/systemd/` for the application user.
- **SQLite database**: Stored in `data/db.sqlite3` with WAL/SHM files alongside it. The `data/` bind mount preserves all of them across rebuilds.
- **Static files**: `manage.py collectstatic` writes to `staticfiles/` on the host. Apache serves this directory directly at `/static/`.
- **Media files**: User uploads go to `media/`, also served directly by Apache at `/media/`.
- **Apache file access**: Apache must be able to traverse the checkout path and read `staticfiles/` and `media/`. Set ownership, group membership, or ACLs accordingly.
- **SAML config**: Bind-mounted read-only into the container. Configure SP entity ID, ACS URL, and certificates in `yvideo/saml_config/`.
- **Gunicorn**: Runs with `--preload` so the legacy dump scheduler starts once in the master process. Set `WORKERS` and `THREADS` in `.env`; tune `WORKERS` based on available CPU cores, and only increase `THREADS` if requests spend significant time waiting on the DB or other I/O.
- **SELinux hosts**: The container Quadlet sets `SecurityLabelDisable=true` so the app and Apache can share the same host directories without relabeling them for container-only access.
