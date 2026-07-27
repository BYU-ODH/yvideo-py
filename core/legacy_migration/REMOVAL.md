# Removing legacy_migration

`core/legacy_migration` exists only to migrate content from the legacy
y-video system. Once that migration is complete, deleting this package is
not enough — it also pulled in infrastructure outside this directory that
has no other purpose. Remove all of it in the same change:

1. **`Dockerfile`** — remove the `openssh-client` apt-get install block. It
   was added solely so `core/legacy_migration/remote_files.py` could shell
   out to `ssh`/`scp` to reach the legacy media host.
2. **`deploy/quadlet.container.in`** — remove the
   `Volume=@DEPLOY_HOME@/.ssh:/root/.ssh:ro` line. It mounts the deploy
   user's own SSH keys and `known_hosts` into the container solely so those
   `ssh`/`scp` calls could authenticate. Leaving it in place after this
   feature is gone means the container keeps read access to the deploy
   user's private key for no reason — an unnecessary credential exposure.
3. **`deploy/common.sh`** — remove the `@DEPLOY_HOME@` substitution in
   `render_template()` if nothing else in the templates uses it once step 2
   is done.
4. **`DEPLOY.md`** — remove the "Legacy migration SSH access" bullet under
   "Key configuration notes".
5. **`gunicorn.conf.py` and `deploy/entrypoint.sh`** — remove the migration
   worker hooks and stop passing the Gunicorn config if it has no other
   settings left.
6. **`yvideo/settings.py` and `yvideo/secret_settings_template.py`** — remove
   `LEGACY_MIGRATION_WORKER_LOCK_PATH`.
7. Check for any cron/systemd timers or management-command invocations
   (e.g. `core/management/commands/process_legacy_migration_jobs.py`) that
   schedule legacy migration work, and remove those triggers too.

None of this infrastructure has any other consumer — it was added
exclusively to support legacy_migration's remote file access. If you're
deleting this package and skip these steps, the container will keep an
unused SSH client and a live mount of the deploy user's private key.
