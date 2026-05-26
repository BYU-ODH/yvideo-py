# Deployment

Each server runs a single instance of the application behind an Apache
reverse proxy. The application runs as a dedicated Unix user in a Podman
container, with data and static files bind-mounted from the host.

## Architecture

```txt
Apache (443, TLS) ──→ 127.0.0.1:HOST_PORT → <app>.service (user <deploy-user>)
```

Apache serves `/static/` and `/media/` directly from the host filesystem.
All other requests are proxied to the Podman container. See
`deploy/apache-vhost-example.conf` for an example configuration.

## Deployment files

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the app image for Podman (Python 3.13, uv, gunicorn) |
| `.containerignore` | Explicit build-context ignore file used by `podman build` |
| `.env_template` | Template for the `.env` file used by deploy scripts to configure the Unix user, port, and Gunicorn tuning |
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
  --home-dir /home/yvideo-dev \
  --shell /bin/bash \
  yvideo-dev

sudo loginctl enable-linger yvideo-dev
```

The deploy user's home directory lives under `/home/` so that Podman's
rootless storage and the systemd user runtime sit on the user partition
in a conventional location. The application checkout lives separately
under `/srv/`, owned by the same user.

`enable-linger` keeps `systemd --user` running after the SSH session ends.
The dedicated Unix user isolates the application's Podman storage and Quadlet
units from other system services.

### 2. Clone the repo

Create `/srv/<deploy-user>/` (owned by the deploy user) and clone the
repository into `/srv/<deploy-user>/<repo-name>`:

```bash
sudo install -d -o yvideo-dev -g yvideo-dev /srv/yvideo-dev
sudo -iu yvideo-dev
git clone git@github.com:BYU-ODH/yvideo-py.git /srv/yvideo-dev/yvideo-py
cd /srv/yvideo-dev/yvideo-py
git checkout main
```

### 3. Create `.env`

```bash
cp .env_template .env
chmod 600 .env
```

`chmod 600` ensures the file (which can hold tuning values referenced by
other services) is readable only by the deploy user.

Edit `.env` and set at least:

- `DEPLOY_USER=yvideo-dev`
- `APP_NAME=yvideo-dev`
- `HOST_PORT=8001`
- `WORKERS=2`
- `THREADS=2`

The deployment scripts enforce `DEPLOY_USER` at runtime. If someone runs
`deploy.sh`, `install_quadlet.sh`, or `manage.sh` from the wrong Unix
user, the script exits instead of touching the wrong instance.

`APP_NAME` becomes all of the following:

- The systemd user service name: `yvideo-dev.service`
- The Podman container name: `yvideo-dev`
- The local image tag: `localhost/yvideo-dev:latest`

### 4. Create `secret_settings.py`

```bash
cp yvideo/secret_settings_template.py yvideo/secret_settings.py
chmod 600 yvideo/secret_settings.py
```

`chmod 600` ensures secrets (`SECRET_KEY`, OIDC client secret, API
credentials) are readable only by the deploy user — never world-readable
on the host, even though the file is also bind-mounted read-only into
the container.

Edit `secret_settings.py`. At minimum set:

- `DEBUG = False`
- `ALLOWED_HOSTS = ["example.com"]` (use your actual domain)
- `SECRET_KEY = "<unique random value>"`
- `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` — tells
  Django that requests are HTTPS when Apache forwards them over plain
  HTTP to gunicorn. Apache must set `X-Forwarded-Proto: https` (see the
  example vhost) and must strip any incoming copy of that header from
  client requests so it can't be spoofed.
- `CSRF_TRUSTED_ORIGINS = ["https://example.com"]` — list of origins
  Django will accept CSRF tokens from. Required when behind a TLS proxy,
  because Django sees the request scheme as `http` until
  `SECURE_PROXY_SSL_HEADER` is honored, and CSRF checks compare against
  the public origin.
- `API_CLIENT_ID`, `API_CLIENT_SECRET`, and all `API_*_URL` values
- All `OIDC_*` values from your identity provider

### 5. Protect the SQLite database files

The SQLite database lives at `data/db.sqlite3` (with `-wal` / `-shm`
sidecars). Once it exists, lock it down:

```bash
chmod 600 data/db.sqlite3 data/db.sqlite3-wal data/db.sqlite3-shm 2>/dev/null || true
```

The deploy entrypoint also enforces these permissions on every start.

### 6. Render and install the container Quadlet

```bash
bash deploy/install_quadlet.sh
systemctl --user start yvideo-dev.service
```

`install_quadlet.sh` creates or updates:

- `~/.config/containers/systemd/yvideo-dev.container`

The deploy script builds the image directly with `podman build`
(`.build` Quadlet units require Podman 5.0+; we target older hosts).

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
sudo -iu yvideo-dev
cd /srv/yvideo-dev/yvideo-py
git fetch origin
git checkout my-branch
bash deploy/deploy.sh my-branch
```

### What `deploy.sh` does

1. Verifies that the checked-out local branch matches the required `<branch>` argument and exits if it does not.
2. Runs `git fetch origin`.
3. Runs `git reset --hard origin/<branch>` so the checkout exactly matches the remote branch.
4. Renders and installs the fresh Quadlet container unit from `.env`.
5. Runs `podman build` to rebuild the local image from the checkout (pulling a fresh base image).
6. Runs `systemctl --user restart <app-name>.service`.
7. Runs `podman image prune -f` to clean up unused images.

## Viewing logs and status

```bash
sudo -iu yvideo-dev
systemctl --user status yvideo-dev.service
journalctl --user -u yvideo-dev.service -f
```

## Running management commands

```bash
sudo -iu yvideo-dev
cd /srv/yvideo-dev/yvideo-py
bash deploy/manage.sh showmigrations  # list migration status (applied vs pending)
bash deploy/manage.sh shell           # open an interactive Django shell in the container
```

## Key configuration notes

- **Dedicated Unix user**: The application runs under a dedicated Unix user (e.g. `yvideo-dev`) with its home directory at `/home/<user>/` and its checkout at `/srv/<user>/<repo-name>/`. The scripts enforce this with `DEPLOY_USER` and a checkout ownership check.
- **Restrictive file permissions**: `.env`, `yvideo/secret_settings.py`, and the SQLite database files are kept at `chmod 600` so secrets and the database are readable only by the deploy user. The deploy entrypoint reapplies `600` to the database files on each container start.
- **Rootless services**: The deployment scripts refuse to run as `root`. All Quadlets install into `~/.config/containers/systemd/` for the application user.
- **SQLite database**: Stored in `data/db.sqlite3` with WAL/SHM files alongside it. The `data/` bind mount preserves all of them across rebuilds.
- **Static files**: `manage.py collectstatic` writes to `STATIC_ROOT` (as set in `settings.py`) on the host. Apache serves this directory directly at `/static/`.
- **Media files**: User uploads go to `media/`, also served directly by Apache at `/media/`.
- **Apache file access**: Apache must be able to traverse the checkout path and read `staticfiles/` and `media/`. Set ownership, group membership, or ACLs accordingly.
- **Gunicorn**: Runs with `--preload` so the legacy dump scheduler starts once in the master process. Set `WORKERS` and `THREADS` in `.env`; tune `WORKERS` based on available CPU cores, and only increase `THREADS` if requests spend significant time waiting on the DB or other I/O.
- **SELinux hosts**: The container Quadlet sets `SecurityLabelDisable=true` so the app and Apache can share the same host directories without relabeling them for container-only access.
