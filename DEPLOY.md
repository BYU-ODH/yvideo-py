# Deployment

Each server runs a single instance of the application behind an Apache
reverse proxy. The application runs as a dedicated Unix user in a Podman
container, with data and static files bind-mounted from the host.

Deploys are pull-based: a per-user systemd timer polls the configured
branch every minute, and when origin advances, the deploy user fetches,
hard-resets the checkout, and rebuilds.

## Architecture

```txt
Apache (443, TLS) ──→ 127.0.0.1:HOST_PORT → <app>.service (user <deploy-user>)

<deploy-user>-deploy.timer → <deploy-user>-deploy.service → poll-and-deploy.sh → deploy.sh
```

Apache serves `/static/` and `/media/` directly from the host filesystem.
All other requests are proxied to the Podman container. See
`deploy/apache-vhost-example.conf` for an example configuration.

## Deployment files

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the app image for Podman (Python 3.13, uv, gunicorn) |
| `.containerignore` | Explicit build-context ignore file used by `podman build` |
| `.env_template` | Template for the `.env` file used by deploy scripts to configure the Unix user, port, branch, and Gunicorn tuning |
| `deploy/quadlet.container.in` | Template for the Quadlet container unit |
| `deploy/deploy.service.in` | Template for the user-level `oneshot` service that runs `poll-and-deploy.sh` |
| `deploy/deploy.timer.in` | Template for the user-level timer that fires the deploy service every minute |
| `deploy/common.sh` | Shared helpers for loading `.env`, validating values, and locating Quadlet and user-systemd paths |
| `deploy/install_quadlet.sh` | Renders and installs the Quadlet container unit (run on each deploy) |
| `deploy/install_deploy_timer.sh` | Renders and installs the deploy service+timer into `~/.config/systemd/user/` (run once at initial setup) |
| `deploy/manage.sh` | Runs Django management commands inside the running container |
| `deploy/entrypoint.sh` | Container entrypoint: runs migrate, collectstatic, starts gunicorn |
| `deploy/poll-and-deploy.sh` | Fetches `origin/$BRANCH` and, if it advanced, hard-resets and execs `deploy.sh` |
| `deploy/deploy.sh` | Refreshes the Quadlet, rebuilds the image, and restarts the container service |
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

`enable-linger` keeps `systemd --user` running after the SSH session
ends — this is required because the deploy timer runs entirely inside
the user manager.

### 2. Clone the repo

Create `/srv/<deploy-user>/` (owned by the deploy user) and clone the
repository into `/srv/<deploy-user>/<repo-name>`:

```bash
sudo install -d -o yvideo-dev -g yvideo-dev /srv/yvideo-dev
sudo -iu yvideo-dev
git clone https://github.com/BYU-ODH/yvideo-py.git /srv/yvideo-dev/yvideo-py
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
- `BRANCH=main` to pin (typical for prod/staging), or leave blank to track whatever branch is currently checked out (typical for dev — `git checkout other-branch` and the next poll redeploys)
- `WORKERS=2`
- `THREADS=2`

The deployment scripts enforce `DEPLOY_USER` at runtime. If someone runs
`deploy.sh`, `install_quadlet.sh`, `install_deploy_timer.sh`, or
`manage.sh` from the wrong Unix user, the script exits instead of
touching the wrong instance.

`APP_NAME` becomes all of the following:

- The container systemd user service name: `yvideo-dev.service`
- The deploy timer/service: `yvideo-dev-deploy.timer` / `yvideo-dev-deploy.service`
- The Podman container name: `yvideo-dev`
- The local image tag: `localhost/yvideo-dev:latest`

`BRANCH` controls what the deploy timer tracks:

- **Set** (e.g. `BRANCH=main`) — the timer pins this deployment to that
  branch. Switching the checkout to another branch locally will not
  redirect the timer; the next poll will reset back to `origin/$BRANCH`.
- **Blank/unset** — the timer follows whatever branch is currently
  checked out. Run `git checkout other-branch` and the next poll will
  fetch `origin/other-branch` and deploy it. Useful on dev boxes.

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

### 6. Install the container Quadlet and start the service

```bash
bash deploy/install_quadlet.sh
systemctl --user start yvideo-dev.service
```

`install_quadlet.sh` creates or updates:

- `~/.config/containers/systemd/yvideo-dev.container`

The deploy script builds the image directly with `podman build`
(`.build` Quadlet units require Podman 5.0+; we target older hosts).

### 7. Install the deploy timer

```bash
bash deploy/install_deploy_timer.sh
```

This renders and installs:

- `~/.config/systemd/user/yvideo-dev-deploy.service` — a oneshot service
  that runs `deploy/poll-and-deploy.sh`.
- `~/.config/systemd/user/yvideo-dev-deploy.timer` — fires the service
  30s after boot and every 60s thereafter.

The installer also runs `systemctl --user daemon-reload` and
`systemctl --user enable --now <app>-deploy.timer`, so the timer is
active immediately and persists across reboots (because of `linger`).

### 8. Seed demo data when needed

Only do this once when initially setting up the instance. The SQLite database
lives on the host bind mount and persists across deploys.

```bash
bash deploy/manage.sh seed_demo_data
```

## How deploys happen

**All deployments come from a git branch on `origin`.** The deploy
process hard-resets the checkout to `origin/<branch>` on every run, so
any uncommitted edits to tracked files and any local-only commits are
destroyed. Untracked files and files in `.gitignore` (including
database, secrets, env, etc.) persist. To ship a change, push it to the
branch this deployment tracks — never edit tracked files directly on the
server.

Once the timer is installed, deploys are automatic:

1. Every minute, `<app>-deploy.timer` triggers `<app>-deploy.service`.
2. That service runs `deploy/poll-and-deploy.sh` as the deploy user
   inside the user systemd manager — no namespace hardening, full
   access to `~/.config/`, `/run/user/$UID`, and the rootless Podman
   storage.
3. `poll-and-deploy.sh` resolves the target branch: `$BRANCH` from
   `.env` if set, otherwise the currently checked-out branch (errors if
   `BRANCH` is unset and HEAD is detached). It then runs
   `git fetch --quiet origin <branch>`. If `HEAD` already matches
   `origin/<branch>`, it exits without doing anything.
4. Otherwise it `git reset --hard origin/<branch>` and execs
   `deploy/deploy.sh`.
5. `deploy.sh` runs `install_quadlet.sh`, `podman build`,
   `systemctl --user restart <app>.service`, and `podman image prune -f`.

To push a new release: merge to the configured `BRANCH` on GitHub. The
next timer tick picks it up.

### Watching deploys

```bash
sudo -iu yvideo-dev
systemctl --user status yvideo-dev-deploy.timer
journalctl --user -u yvideo-dev-deploy.service -f
```

### Forcing a deploy now

```bash
sudo -iu yvideo-dev
systemctl --user start yvideo-dev-deploy.service
```

### Switching branches on a dev deployment

If `BRANCH` is left blank in `.env`, the timer follows the currently
checked-out branch. To redirect the deployment to a different branch:

```bash
sudo -iu yvideo-dev
cd /srv/yvideo-dev/yvideo-py
git fetch origin
git checkout other-branch
# next timer tick (≤60s) will fetch origin/other-branch and deploy it,
# or force it now:
systemctl --user start yvideo-dev-deploy.service
```

### Hotfixing a pinned deployment

When `BRANCH` is pinned (e.g. `BRANCH=main` on prod), `git checkout`
won't redirect the timer — the next tick resets back to `origin/main`.
To ship a hotfix without merging it into the tracked branch yet, push
a hotfix branch to `origin` and temporarily repoint `BRANCH` in `.env`.
`.env` is re-read on every poll, so no service restart is needed.

```bash
# 1. On your laptop: push the hotfix branch to origin.
git push origin hotfix-urgent-thing

# 2. On the server: point the timer at the hotfix branch.
sudo -iu yvideo-dev
cd /srv/yvideo-dev/yvideo-py
sed -i 's/^BRANCH=.*/BRANCH=hotfix-urgent-thing/' .env
systemctl --user start yvideo-dev-deploy.service  # deploy now
```

Once the fix has been merged back into the normal release branch and
that branch contains everything the hotfix had, restore the original
`BRANCH` value so the deployment goes back to tracking the release
branch:

```bash
sudo -iu yvideo-dev
cd /srv/yvideo-dev/yvideo-py
sed -i 's/^BRANCH=.*/BRANCH=main/' .env
systemctl --user start yvideo-dev-deploy.service
```

Leaving a deployment pointed at a hotfix branch indefinitely is a
footgun — future merges to `main` won't deploy until you switch back.

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
- **Pull-based deploys**: The deploy timer runs entirely inside the deploy user's own `systemd --user` instance, so there is no external runner with a hardened mount namespace fighting Podman, Quadlet, or rootless container storage. `loginctl enable-linger <user>` is required for the timer to fire when no one is logged in.
- **Restrictive file permissions**: `.env`, `yvideo/secret_settings.py`, and the SQLite database files are kept at `chmod 600` so secrets and the database are readable only by the deploy user. The deploy entrypoint reapplies `600` to the database files on each container start.
- **Rootless services**: The deployment scripts refuse to run as `root`. All Quadlets install into `~/.config/containers/systemd/` and the deploy timer into `~/.config/systemd/user/` for the application user.
- **SQLite database**: Stored in `data/db.sqlite3` with WAL/SHM files alongside it. The `data/` bind mount preserves all of them across rebuilds.
- **Static files**: `manage.py collectstatic` writes static assets (with hash added to filenames) and
  `staticfiles.json` to `STATIC_ROOT` (as set in `settings.py`) on the host.
  Apache serves this directory directly at `/static/`. Templates must use
  Django's `{% static %}` tag so deployed pages reference the hashed filenames.
- **Media files**: User uploads go to `media/`, also served directly by Apache at `/media/`.
- **Apache file access**: Apache must be able to traverse the checkout path and read `staticfiles/` and `media/`. Set ownership, group membership, or ACLs accordingly.
- **Gunicorn**: Runs with `--preload` so the legacy dump scheduler starts once in the master process. Set `WORKERS` and `THREADS` in `.env`; tune `WORKERS` based on available CPU cores, and only increase `THREADS` if requests spend significant time waiting on the DB or other I/O.
- **SELinux hosts**: The container Quadlet sets `SecurityLabelDisable=true` so the app and Apache can share the same host directories without relabeling them for container-only access.
