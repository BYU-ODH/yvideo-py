# IC Mode: replacing the IC Player with Y-video (#246)

Status: **implemented** on branch `246-replace-ic-player-with-ic-mode-on-y-video` (see §14 for where the code differs from this plan). Deployment: [DEPLOY_IC.md](DEPLOY_IC.md).

## 1. Goals and non-goals

### Goals
- International Cinema (IC) staff prepare films in Y-video with the normal editor. Playback in the booth uses the **same database rows, the same `Content.get_player_json()`, and the same `AnnotationPlayer.js` at the same git commit** as the preview on production. No annotations are exported, copied or hand-edited along the way.
- Production manages the high-res projection masters and gets them onto the booth machine. Nobody copies files by hand.
- **Everything playback needs is stored on the booth**: projection mp4, annotations, VTT subtitles, JS/CSS/fonts. A network outage, or production being down, cannot affect a showing.
- The booth is a **read-only, userless** mirror. It has no logins, no editor and no admin.
- Two-display operation: an operator control screen, and an output screen that the projector mirrors.

### Non-goals (for now)
- Personal IC staff instances. The design keeps them possible (see §11) but doesn't build them.
- Multiple booths. The data model allows more than one, but only one is deployed.
- Live editing at the booth. The Electron app's "annotation mode / reload JSON" workflow is replaced by previewing on production and syncing.

### Decisions already made
| Question | Decision |
|---|---|
| Transport | **The booth only ever calls production**; nothing connects to the booth. Data always flows by **booth download + verification** (§7.1) |
| Sync triggers | **Pull-only.** The booth checks production every 30 s and pulls changes; the projectionist can **Sync now** at the booth, with a live success indicator. Production can't push (§7.1, §7.5–7.8) |
| Booth code updates | The booth's deploy timer polls GitHub like production's, but not during `IC_PLAYBACK_HOURS` (§8.4) |
| Booth writes | Booth is read-only: no HTTP request can change mirrored data. The only HTTP write is the operator's "Sync now" (§8.1) |
| Booth users | Booth is userless: no login, no admin |
| Displays | Two monitors; the projector mirrors the output monitor |
| Editing file | **Transcoded from the projection master** |
| Subtitles | Burned-in still supported; **VTT tracks preferred** from now on |
| Booth count | One. Personal instances only if deployment is trivial |
| Booth platform | Ubuntu (Wayland session) + latest Google Chrome on a Dell with **Intel UHD Graphics 630** (Comet Lake, `i915`) (§6.1) |
| File specs | Projection master = maximum available resolution/bitrate (no cap); streaming/editing copy = SD |
| Master retention | **Production deletes its copy of a master as soon as the SD transcode is complete and the booth's copy is downloaded and checksum-verified.** The booth then holds the only copy (§5.7) |
| Upload access | IC editors upload masters only for resources they can access (`can_access_resource`) |
| Fonts | **Open/free fonts only** (e.g. Google Fonts, self-hosted) |
| Transcoding | Runs on the production server at low CPU and I/O priority |
| Branding | **Patron-facing** output matches **ic.byu.edu** (logo committed to repo, fonts, monochrome palette), vendored for offline use. Operator/staff pages are Y-video's UI; the **booth app is dark mode** (§6.6) |
| Who manages IC | **IC editors** manage everything (programs, IC playlists, projection uploads) through the app, not the Django admin |

## 2. Current state (relevant facts)

- The Electron IC Player already shares the core player code. `IC_player/src/{AnnotationPlayer,SubtitleSidebar,utils,video-geometry}.js` and `AnnotationPlayer.css` are symlinks into `core/static/`. The IC-specific code is only `IC-player.{js,html,css}` (file picking, `.icf` handling, copying to the Desktop) and the branding assets.
- `AnnotationPlayer.parseICLegacyAnnotations()` already reads the legacy IC JSON (censor → blur + anchor points, blur → screen blur).
- Playback auth: `player()` → `User.get_resource_filekey()` mints a 24h `ResourceFileKey` bound to one user, and `stream_file` → `_file_response()` serves byte ranges (#529). A userless booth needs its own stream path.
- `LoginRequiredMiddleware` is global. `core/tests/test_view_permissions.py` requires every routed view to carry a permission decorator or an explicit exemption.
- `noncsrf_base.html` loads Google Fonts, and `header.html` links to external BYU pages. **IC templates must not extend these bases**, or the booth would depend on the network.
- `core.context_processors.feature_flags` reads `request.user`, so the booth must keep `AuthenticationMiddleware` (an `AnonymousUser` is fine).
- `deploy/apache-vhost-example.conf` serves all of `/media/` with `Require all granted`. **Projection masters must therefore live outside `MEDIA_ROOT`.** The `/media/` exposure itself is a separate issue (see §13).
- The only background-worker pattern today is the legacy-migration worker that gunicorn spawns from `when_ready` (`gunicorn.conf.py`), and it's marked for removal. The ssh/scp plumbing in the Dockerfile and quadlet is also legacy-only. This plan depends on neither.
- `ffmpeg`/`ffprobe` are not in the container image.

## 3. Architecture

```txt
PRODUCTION (parent)                                      BOOTH (IS_IC_INSTANCE=True)
────────────────────                                     ──────────────────────────
IC editors edit content in Playlist.is_ic                same git SHA, same AnnotationPlayer.js
IC editors upload projection master (chunked)            ROOT_URLCONF = yvideo.urls_ic
  └─ ffprobe + faststart remux                           no auth, no admin, no data writes
  └─ low-priority transcode → SD ResourceFile            local SQLite mirror (IC tables only)
/ic/manage/: programs, masters, readiness                local projection masters + VTT files
/ic/ preview (same player, SD file)                      /ic/          (control + "Sync now", operator monitor)
Check: manifest hash                  ◄── every 30 s ──  /ic/output/   (projector monitor)
Sync API: manifest, fixture, files    ◄── booth pulls ── ic_sync_agent (check 30 s, queued runs)
Run reports                           ◄── booth posts ──
```

Every connection starts on the booth. The booth needs no inbound ports, and production
holds no booth credentials.

### Invariants
1. The booth never receives annotation JSON, only rows. It renders player JSON with the same code.
2. Before a synced program counts as playable, the booth's `sha256(canonical player JSON)` for each content must equal production's. Otherwise the import is rolled back and the previous good mirror stays in place.
3. Every file the booth plays is on local disk and its xxh64 has been verified against production's checksum.
4. A sync never changes the film that's playing. The output holds the item's player data in memory and streams from a URL that names one verified file, so a sync can apply at any time (§7.4).
5. Every trigger (automatic, or "Sync now") runs the same booth-side pipeline. Forcing changes *when* and *how thoroughly* it checks, never *whether* it verifies.

## 4. Data model (PR 1)

### 4.1 Settings (`yvideo/settings.py` + `secret_settings_template.py`)
| Setting | Where | Purpose |
|---|---|---|
| `IS_IC_INSTANCE` (default `False`) | both | switches urlconf, middleware, storage, admin |
| `IC_PROJECTION_ROOT` | both | storage root for masters; **not** under `MEDIA_ROOT`, not aliased by Apache |
| `IC_SYNC_TOKEN` | booth | bearer token presented to production |
| `IC_PARENT_URL` | booth | e.g. `https://yvideo.byu.edu` |
| `FILE_UPLOAD_TEMP_DIR` | prod | bind-mounted path (`/app/var/upload_tmp`), not the container's `/tmp` |

Add `Volume=@IC_PROJECTION_ROOT@:/app/ic_projection` to `deploy/quadlet.container.in` (rendered only when it's set).

### 4.2 `ResourceFile`
- `is_projection_file = BooleanField(default=False)`
- Storage: projection files use `ic_projection_storage` (`FileSystemStorage(location=IC_PROJECTION_ROOT)`). Either use a callable storage, or keep the masters in a separate `ProjectionFile` FileField. Decide during implementation; the callable is simpler if Django's `FieldFile` handles it cleanly.
- Probe metadata (filled in by ffprobe, read-only in admin): `duration_seconds`, `width`, `height`, `frame_rate`, `video_codec`, `audio_codec`, `bitrate`, `faststart`, `probed_at`.
- `derived_from = ForeignKey("self", null=True, SET_NULL, related_name="derivatives")`, set on the transcoded editing file.
- `UniqueConstraint(fields=["resource"], condition=Q(is_projection_file=True), name="one_projection_file_per_resource")`.
- The existing `burned_in_subtitles_language` is used to record burned-in subtitles on the master.
- **Guard:** a projection file must never be a `Content.resource_file`. Enforce it in `Content.clean()`, and exclude projection files from every content-creation chooser (`create_from_resource_form`, etc.). Otherwise a ~10 GB master could be streamed to a classroom.

### 4.3 IC editor role
- Add a Django group `IC_EDITOR_GROUP_NAME = "IC Editors"`, created in `CoreConfig.ready()` like `LAB_ASSISTANT_GROUP_NAME` (`core/apps.py`), with a factory trait and a `dev_seed` user.
- `User.is_ic_editor` property: in the group, or `is_admin`.
- Superusers add people to the group in the existing `UserAdmin`. That's the only IC task that stays in Django admin, together with `ICInstance` tokens (§7.2).
- What IC editors can do (all in the app, §4.7):
  - Create, edit and delete `ICProgram`s and their items.
  - Mark playlists they can edit as IC (`is_ic`).
  - Upload or replace projection masters, and switch IC content to the derived SD file.
  - View the readiness/sync dashboard and preview programs in `/ic/`.
- Projection uploads also require `user.can_access_resource(resource)`, the same gate as attaching files to content. If IC editors routinely need masters for resources they haven't been granted, a superuser can grant `ResourceAccess`, or we can add `is_ic_editor` to `can_access_resource()` later. That second option is a broad library-wide grant, so it isn't the default.
- Annotation authoring keeps using the normal editor and playlist roles. IC editors author in IC playlists they own or collaborate on.

### 4.4 `Playlist.is_ic`
- `BooleanField(default=False)`.
- Settable by IC editors on playlists they can edit (`can_be_edited_by`), and by superusers in `PlaylistAdmin`. The toggle appears in `playlist_settings` only for IC editors. Non-IC users never see it, and the update view ignores the field from anyone else.
- Unsetting `is_ic` is blocked while any of the playlist's contents are used in a syncable program (show which programs).
- It marks Content that's eligible for IC programs.

### 4.5 `ICProgram` / `ICProgramItem`
```python
class ICProgram(models.Model):
    name = models.CharField(max_length=255)          # "Amélie — Nov 12–15"
    start_date = models.DateField()
    end_date = models.DateField()                    # CheckConstraint end >= start
    notes = models.TextField(blank=True)
    created_at / updated_at

    @classmethod
    def active(cls, on=None): ...                    # start <= timezone.localdate() <= end
    @classmethod
    def syncable(cls, on=None): ...                  # end_date >= today - 1 (active + upcoming)
    def readiness(self) -> list[ReadinessProblem]: ...

class ICProgramItem(models.Model):
    program = models.ForeignKey(ICProgram, CASCADE, related_name="items")
    content = models.ForeignKey(Content, PROTECT,
                                limit_choices_to={"playlist__is_ic": True})
    position = models.PositiveIntegerField()         # unique with program

    class Mode(models.TextChoices):
        ONCE = "once"     # play to end
        LOOP = "loop"     # loop until the operator advances (pre-show announcements)
    mode = models.CharField(max_length=4, choices=Mode.choices, default=Mode.ONCE)
    auto_advance = models.BooleanField(default=False)  # ONCE: go to next item at end

    class Subtitles(models.TextChoices):
        VTT = "vtt"               # show content.default_subtitle_track on output
        BURNED_IN = "burned_in"   # master has burned-in subs; show no track
        NONE = "none"             # announcements, English films, etc.
    subtitles = models.CharField(max_length=9, choices=Subtitles.choices,
                                 default=Subtitles.VTT)

    def projection_file(self): ...  # content.resource.resource_files(is_projection_file=True)
```

Items point at `Content` rather than `Resource`, so the resource, annotation set, default subtitle track and flags are the same ones staff preview in the normal player.

### 4.6 Readiness
`ICProgram.readiness()` returns structured problems. Each one is either an **error** (blocks "ready") or a **warning**:

| Check | Severity |
|---|---|
| Content is URL/YouTube-only (needs the network) | error |
| No projection file on the content's resource | error |
| Projection file not probed, or not faststart | error |
| Codec the booth can't decode: AV1 (no hardware decode on UHD 630), or HEVC before the spike confirms Chrome hardware decode (§6.1). H.264/AAC is always OK | error |
| HDR master (`color_transfer` = `smpte2084`/`arib-std-b67`) | warning: Chrome on Linux won't tone-map for an SDR projector, so HDR looks washed out; prefer an SDR master |
| Master exceeds what the booth has been verified to play smoothly (resolution × frame rate × bitrate above a recorded `IC_BOOTH_VERIFIED_MAX`) | warning: rehearse on the booth before opening night |
| `content.resource_file.derived_from` ≠ the projection file (annotations may be timed against a different source) | error, overridable with an explicit `timing_verified` checkbox on the item for legacy films |
| `subtitles == VTT` but no `default_subtitle_track` | error |
| `subtitles == BURNED_IN` but master has no `burned_in_subtitles_language` | warning |
| No annotation set on content | warning (some films need none) |
| Transcode of editing file still pending/failed | error |

The program list shows ✅/⚠️/❌ with the problem list in a tooltip, and the item editor shows each row's problems. This covers the issue's "indicator whether there is a projection file" requirement and more.

### 4.7 IC management UI (production only)
IC editors aren't superusers, so IC management lives in the app under `/ic/manage/` (Django templates + HTMX, following `ui-ux-standards.md` and the existing breadcrumb and messages patterns). Django admin is not used for this.

| Page | What it does |
|---|---|
| `/ic/manage/` | Dashboard: programs (active, upcoming, past) × readiness × booth sync state (§7.8); media job queue with progress |
| `/ic/manage/programs/new/`, `/ic/manage/programs/<id>/` | Program CRUD: name, dates, notes; items added from a picker of contents in IC playlists the editor can see; drag to reorder (reuse the drag pattern from track `update_stack_positions`); per-item mode, auto-advance, subtitles, `timing_verified`; live readiness panel; "Preview" opens the production `/ic/program/<id>/` |
| `/ic/manage/resources/` | Resources used by IC contents and those the editor can access, each with: projection master status (none / uploading / processing / ready / failed), probe summary (resolution, codec, bitrate, duration, size), derived SD file status, and "switch IC contents to the derived file" |
| `/ic/manage/resources/<id>/projection-upload/` | Chunked upload (§5.1) |

- Permission decorators in `core/permissions.py`:
  - `ic_editor_required` (role)
  - `ic_program_write_required` (object: `ICProgram.can_be_edited_by(user)` → `user.is_ic_editor`)
  - `ic_projection_upload_required` (object: resource; `is_ic_editor` and `can_access_resource`)

  Register all of them with `test_view_permissions.py`.
- History: register `ICProgram`/`ICProgramItem` with `django-reversion` and wrap the manage views in `reversion.create_revision()` so program changes are attributable. Spoofing works as elsewhere, and the spoof log records the real user.
- Header: add an "International Cinema" entry for `is_ic_editor` users (plain Y-video styling, §6.6).
- Django admin still registers the IC models (read/write for superusers, for support and debugging), plus `is_ic` in `PlaylistAdmin` and `is_projection_file`/probe fields in `ResourceFileAdmin`.

### 4.8 Tests
- Constraints, the `active()`/`syncable()` boundaries (use `timezone.localdate()`), each readiness rule, and the projection-file exclusion from content forms.
- Role tests: IC editor vs. instructor vs. lab assistant vs. superuser for every `/ic/manage/` view.
- The `is_ic` toggle is ignored when sent by a non-IC editor, and unsetting it is blocked while the playlist is in use.

## 5. Projection master pipeline on production (PR 3)

### 5.1 Chunked, resumable upload
A single multi-GB (typically ~10 GB) POST through Apache → gunicorn is too fragile, so uploads go in chunks.

- Page: `/ic/manage/resources/<resource_id>/projection-upload/` (IC editors with access to the resource, §4.3).
- Masters are uncapped (decision: maximum available resolution/bitrate), but in practice they're about **10 GB on average and rarely over 20 GB**. Design for that, while still handling an occasional larger file correctly (nothing in the pipeline assumes a size limit). The upload page shows size, throughput and ETA, warns before navigating away, and resumes automatically if the browser tab is reopened (the upload id is kept in `localStorage`, keyed by resource and file name+size+mtime).
- Before accepting the first chunk, check free space on `IC_PROJECTION_ROOT` (needs about 2× the file size: the `.part`, a possible faststart remux, and the SD derivative) and refuse with a clear message if there isn't enough.
- JS slices the `File` into about 64 MB chunks:
  - `POST .../uploads/` creates an `ICUpload(id=uuid, resource, expected_size, owner, received_bytes, xxh64_state)` and returns its id.
  - `HEAD .../uploads/<id>/` returns `received_bytes`, so the upload can resume after a refresh or dropped connection.
  - `PUT .../uploads/<id>/?offset=N` appends the raw body to `IC_PROJECTION_ROOT/.incoming/<id>.part`. It returns 409 if `offset != received_bytes`, and updates the incremental xxh64 so finalizing doesn't re-read ~10 GB.
  - `POST .../uploads/<id>/finalize/` runs §5.2.
- Abandoned uploads are cleaned up after 7 days by the media worker (§5.4).

### 5.2 Finalize
1. Check that `received_bytes == expected_size`, and that the checksum is unique (`ResourceFile.checksum` is already unique).
2. Run `ffprobe -v error -show_format -show_streams -of json`. Reject files with no video stream or a container other than MP4/M4V. Reject codecs the booth can't decode (§4.6). Store the metadata. There's no resolution or bitrate cap.
3. If the `moov` atom isn't at the front, remux losslessly with `ffmpeg -i in -map 0 -c copy -movflags +faststart out`.
4. Atomically move to `IC_PROJECTION_ROOT/<resource_id>/<checksum>.mp4`, and create or replace the `ResourceFile(is_projection_file=True)` row. A replaced master marks dependent programs not-ready until the new transcode finishes and the booth re-syncs.
5. Queue a transcode job (§5.3).
6. Re-uploading a master whose bytes were released (§5.7) matches the existing row's checksum. That **restores** the existing `ResourceFile` (same pk, same checksum, so booth records and fingerprints stay valid) instead of failing the checksum-uniqueness validator.

### 5.3 Transcode the SD editing file from the master (decision #5)
Because the editing/stream file is generated from the master, both files share a timeline by construction. That removes the biggest source of annotation drift.

```bash
ffmpeg -i master.mp4 -map 0:v:0 -map 0:a:0? \
  -vf "scale=-2:'min(480,ih)',setsar=1" -fps_mode passthrough \
  -c:v libx264 -preset medium -crf 23 -maxrate 1500k -bufsize 3000k \
  -profile:v main -pix_fmt yuv420p \
  -c:a aac -ac 2 -b:a 128k -movflags +faststart -threads 2 stream.mp4
```
- SD means 480 lines (854×480 for 16:9, with the width following the aspect ratio), about 1–1.5 Mbps, stereo AAC. Main profile and yuv420p decode everywhere. 10-bit/HDR masters get tone-mapped or converted by the `pix_fmt`; check that colors on HDR masters are acceptable for editing purposes.
- `-fps_mode passthrough` and no `-ss`/`-t` keep timestamps identical. After transcoding, probe again and **assert |duration(stream) − duration(master)| < 1 frame**, and that the first video timestamps match. Otherwise fail the job.
- Result: `ResourceFile(resource=…, version=f"ic-sd-{master.checksum[:8]}", derived_from=master)`.
- Offer a "switch content to the derived file" action for IC contents that currently use an older file, with a warning that existing annotations may need re-timing if the old file came from a different source.
- **Low priority on production** (decision): see §5.4. Run one job at a time with `-threads 2`, so even with maximum-bitrate 4K masters the transcode can't take every core. Expect roughly 0.5–2× real time depending on master resolution and server cores, and show progress in the dashboard by parsing ffmpeg's `-progress pipe:1` output.

### 5.4 Media job worker
Add a `MediaJob(kind, status, payload, log, created_at, started_at, finished_at)` table and `manage.py process_media_jobs`. Kinds: `probe`, `faststart`, `transcode`, `release_master` (§5.7) and `cleanup_uploads`. (Booths learn about changes by checking production, §7.5, so there's no notify job.)

How to run the worker:
- **Preferred:** a separate quadlet/systemd user service that runs the same image with a different command. Its lifecycle is independent of gunicorn, so it doesn't extend the legacy-only `gunicorn.conf.py` pattern.
- Alternative: generalize `gunicorn.conf.py`'s `when_ready` so it doesn't vanish when legacy migration is removed.

Either way, recover stale `running` jobs at startup (same idea as the legacy worker).

Priority, applied at three layers so it holds even if one is ineffective under rootless Podman:
- Systemd/quadlet: the worker's unit sets `Nice=19`, `IOSchedulingClass=idle`, and `CPUWeight=10` (`[Service]` section; `CPUWeight` needs cgroup v2 CPU delegation for user services, so check on the server with `systemctl --user show -p CPUWeight`).
- Process: the worker launches ffmpeg as `nice -n 19 ionice -c 3 ffmpeg …`. Lowering priority needs no privileges inside the container.
- ffmpeg: `-threads 2`.

The web container keeps default priority, so playback for classes and the editor stays responsive during a transcode. Add a short manual test: run a transcode while scrubbing in the editor and confirm there's no visible slowdown.

### 5.5 Container
Add `ffmpeg` to the Dockerfile `apt-get install`. It also provides `ffprobe`.

### 5.6 Tests
- Chunk offset conflict, resume, and checksum equality with `_calculate_checksum_for_file()`.
- Finalize with a small fixture mp4 (`demo_media/`), including the faststart remux path (build a fixture with moov at the end).
- Transcode duration assertion, mocked for unit tests plus one real ffmpeg integration test marked slow.

### 5.7 Master lifecycle: production releases its copy after the booth verifies (decision)
Production keeps a master only until two things are both true. Then it deletes the bytes, and **the booth's copy is the only one**.

1. The SD derivative's transcode job has **succeeded**, including the duration/timestamp assertion (§5.3).
2. Every `ICInstance` (today: the one booth) has reported, in a sync status POST, that it holds the file **verified by hashing the downloaded bytes** (xxh64 equal to `ResourceFile.checksum`). A cached "already verified" flag isn't enough for the first confirmation.

Implementation:
- The booth's status report lists `{"resource_file_id", "xxh64", "verified_at", "hashed_this_run": true}` for each verified master. Production records it in `ICInstanceFile(instance, resource_file, xxh64, verified_at)`.
- When both conditions hold, production queues `MediaJob(kind="release_master")`. The job re-checks both conditions inside a transaction, deletes the bytes from `IC_PROJECTION_ROOT`, and sets `ResourceFile.master_released_at`.
  - **The row stays**: checksum, size, probe metadata and `derived_from` links. Contents, fingerprints and the booth's mirror don't change.
  - Nothing else deletes masters on production: no admin action, no `ResourceFile.delete()` side effect (guard it so deleting such a row requires a superuser confirmation).
- Manifest file entries gain `"available_from_parent": false` once released. The booth keeps such files and never expects to re-download them.
- **Booth pruning becomes destructive for released masters**, so the rule changes (§7.3 step 4): a released master is pruned only when it's referenced by no syncable program *and* its last program ended more than 30 days ago (configurable). The dashboard warns 7 days ahead: "Booth will delete its only copy of X on <date>; re-upload will be needed to show it again."
- **If the booth loses a released master** (disk failure, accidental deletion, a failed periodic re-hash), the run reports `master_lost`. Every program using it turns ❌ "re-upload required" on the dashboard and on the booth. Re-uploading restores it (§5.2 step 6) and the normal sync brings it back.
- **Integrity of the only copy:** the sync agent re-hashes every released master on the booth once a week, outside showtimes and throttled, and reports the results. A mismatch is `master_lost` (above), surfaced immediately rather than discovered on opening night.
- **Backup is IC's responsibility:** production is no longer a backup. The dashboard and `DEPLOY_IC.md` state this plainly: keep the original source (disc or file) until the film's last showing.
- `ResourceFile` gains `master_released_at = DateTimeField(null=True)`, and readiness gains "master released on production and not verified on the booth" → error (re-upload).

## 6. The IC player: controller + output (PR 4)

The pages are built on production first, where they double as the **preview of exactly what the booth will show**. The booth then reuses the same templates and JS.

### 6.1 Two-window model (decision #4)
- **Control window** (operator monitor): program list, item queue, play/pause, next/previous, scrubber, emergency blank/mute, subtitle toggle, status. It **does not decode video.** It shows state that the output window reports back: current time, item, playing/paused, active annotations, the next upcoming annotation, last sync time.
- **Output window** (second monitor, mirrored to the projector): a fullscreen `AnnotationPlayer` with every on-screen control disabled, a black background, nothing else.
- Communication: the control page opens the output with `window.open()`, then they use `postMessage` with `BroadcastChannel("ic")` as a fallback. Messages are `{cmd: "play"|"pause"|"seek"|"loadItem"|"blank"|"mute"|"subtitles", ...}` from controller to output, and `{state: {...}}` heartbeats every 250 ms from output to controller. This needs no server round-trips, so it's naturally read-only and offline.
- Placing and fullscreening the output: use the **Window Management API** (`getScreenDetails()`, then `requestFullscreen({screen})` on the second screen). Google Chrome supports it. The operator clicks "Open output" once at the start of a session, which provides the needed user activation. On the booth, grant the permission via Chrome policy `WindowManagementAllowedForUrls: ["http://localhost:<port>"]` (§8.4).
- **Spike first (PR 0)** on the real booth, using the **largest real master** IC expects (maximum resolution and bitrate). Known hardware: **Intel UHD Graphics 630** (Comet Lake GT2, `i915` driver), Ubuntu **Wayland** session, Google Chrome stable.
  1. **Hardware decode (VA-API):** install `intel-media-va-driver-non-free` and `vainfo`. Confirm `vainfo` lists `VAProfileH264High` and `VAProfileHEVCMain`/`Main10`. On paper the UHD 630 decodes H.264 and HEVC (8- and 10-bit) in hardware up to 4K, plus VP9, and **has no AV1 hardware decode**.
     - In Chrome, check `chrome://gpu` ("Video Decode: Hardware accelerated") and `chrome://media-internals` while playing (`kVideoDecoderName` should be a VA-API/`VaapiVideoDecoder`-style decoder, not `FFmpegVideoDecoder`/`Dav1d`; dropped frames ≈ 0).
     - Chrome's Linux VA-API support has historically needed feature flags and depended on the windowing backend. Test these combinations and record which one works:
       - (a) Chrome defaults
       - (b) `--enable-features=VaapiVideoDecoder,VaapiVideoDecodeLinuxGL,VaapiIgnoreDriverChecks`
       - (c) (b) plus `--ozone-platform=x11` (XWayland inside the Wayland session)
       - (d) (b) in an "Ubuntu on Xorg" session
     - **HEVC is allowed only if hardware decode works**, because Chrome has no software HEVC decoder. Large 4K H.264 masters may still play in software on the Comet Lake CPU, but treat that as not verified.
     - Record the highest verified resolution × frame rate × bitrate per codec as `IC_BOOTH_VERIFIED_MAX` (§4.6).
  2. **Output window placement on Wayland:** native Wayland doesn't let apps choose window positions. It does let them request fullscreen on a specific output, which is what `requestFullscreen({screen})` needs.
     - Confirm the output window lands fullscreen on screen 2 under the combination chosen in step 1.
     - If native Wayland can't do it, XWayland (`--ozone-platform=x11`) usually can. If neither can, switch the booth to "Ubuntu on Xorg".
     - Pick **one** combination that satisfies steps 1 and 2 together, since decode and window placement both depend on the backend. Bake it into the kiosk unit (§8.4).
  3. **Displays:** the UHD 630 drives up to three displays. Confirm the operator monitor plus the output monitor (mirrored to the projector) at their native modes. Note the projector's resolution: a 4K master on a 1080p projector is downscaled by Chrome, which costs decode work but isn't a problem.
  4. **Disk read:** confirm the booth disk sustains the master bitrate with a sync download running in the background.
  5. **Audio:** confirm Chrome's output reaches the booth sound system, and that surround tracks downmix or pass through as IC expects.

  Fallback if the Window Management route fails: two Chrome instances, control in `--app` mode on screen 1 and output in `--kiosk --window-position=<screen2>` with a separate `--user-data-dir`. They'd then need another channel; the simplest is a `ws://localhost` relay or a localhost SSE endpoint, which adds booth-side machinery.
- Resilience:
  - If output heartbeats stop for 2 s, the controller shows "Output lost" with a **Reopen and resume at hh:mm:ss** button.
  - Closing or refreshing the controller doesn't stop the output; on reload the controller re-attaches via `BroadcastChannel`.

### 6.2 Routes (`core/urls_ic.py`, mounted at `/ic/` on both instances)
| Name | Path | Notes |
|---|---|---|
| `ic:control_index` | `/ic/` | active programs (prod: also upcoming, with readiness) |
| `ic:control` | `/ic/program/<program_id>/` | operator page |
| `ic:output` | `/ic/output/` | output shell; items are loaded by message |
| `ic:item_player_data` | `/ic/item/<item_id>/player-data/` | **GET**, calls `Content.get_player_json()` and adds `fingerprint` and `media_url`; subtitles filtered per `item.subtitles` |
| `ic:item_media` | `/ic/item/<item_id>/media/` | Production preview only: Range stream of the SD file via the shared `_file_response()` |
| `ic:local_file` | `/ic/file/<resource_file_id>/<xxh64>/` | Booth only: Range stream of one verified local master, `Cache-Control: immutable` |
| `ic:status` | `/ic/status/` | booth: sync log + readiness (read-only) |

- On **production**, the item media is the *derived SD file* (bandwidth-friendly preview), and access requires `is_ic_editor`.
- On the **booth**, the item media is the *local projection master*, addressed by its id and checksum (`ic:local_file`). The URL arrives in the player data, so the annotations and the file they're timed against are always fetched as one pair, and the bytes behind it never change (§7.4). Player data requires the program not to have ended (upcoming programs play as rehearsals; see §6.4), and no user.
- `player_data` becomes GET here (the existing endpoint is POST with CSRF only because it lives in the authenticated app). That keeps the booth GET-only.
- Add `ic_*` decorators in `core/permissions.py` and register the views in `test_view_permissions.py`. On the booth urlconf, views are exempt with a documented reason ("booth is userless and bound to localhost").

### 6.3 Frontend files
- `core/templates/core/ic/base.html` is a standalone base: no Google Fonts, no header/footer, no external links, no spoof banner or what's-new. Follow `ui-ux-standards.md` for the controller.
- Styling (§6.6): `core/static/css/ic-output.css` holds the IC brand tokens for the output window, and `core/static/css/dark.css` holds the dark-mode tokens for the controller. The Electron player's CSS and assets are not ported.
- `core/static/js/ic-output.js`:
  - Constructs `AnnotationPlayer({editorMode:false, subtitleSidebar:false, allowFastPlayback:false, disabledControls:[…all…]})`.
  - On `loadItem`: fetch the player data, `loadData(playerData)`, then `setVideoSource(playerData.media_url)`. Captions are rendered as on-screen cues when `item.subtitles == "vtt"`.
  - Handles `ended` for LOOP/ONCE, and posts state heartbeats.
  - Only uses AnnotationPlayer's public API, the same rule `player-wrapper.js` follows.
- `core/static/js/ic-control.js` holds the queue state machine (idle → loaded → playing → ended → next), keyboard shortcuts and the UI.
- Shortcuts (controller): Space play/pause, N next item, P previous item, B emergency blank toggle, M mute, S subtitles toggle, ←/→ ±5 s (skip annotations still apply), O reopen output.
- Projected subtitles: add a `.ic-output` CSS scope with a larger cue font, a raised baseline (projector overscan), and a strong text shadow. Add a controller setting for cue size (stored in `localStorage`, so the booth stays read-only).

### 6.4 Behavior details to get right
- **LOOP items:** restart on `ended` (or set `video.loop`). Test that a skip annotation reaching the end of the file still produces `ended` and doesn't stall.
- **Item transitions:** the output goes to black between items; no player chrome should flash on the projector.
- **No view counting:** the IC views never touch `content.views`.
- **Rehearsal:** the booth may play *upcoming* (synced, not yet active) programs from the controller behind an explicit "Rehearse" toggle, so the projectionist can test before opening night. Expired programs are not playable.

### 6.5 Tests
- View, permission and urlconf tests.
- `tests/js/` unit tests of the controller state machine and message protocol.
- Playwright e2e (`tests/e2e/`): a 2-item program (LOOP announcement → ONCE film, using `demo_media`). Open the output, assert the loop restarts, press N, assert the skip fires and the VTT cue renders. Close the output mid-film, reopen, and assert it resumes near the same time.

### 6.6 Branding: patron-facing output matches ic.byu.edu; booth controller is dark mode
What patrons see follows **ic.byu.edu**; everything else is Y-video in dark mode. The Electron assets (`IC.png`, `ic_round.png`, `filmstrip.png`, Lobster) are dropped.

**What ic.byu.edu uses** (read from the site's HTML/CSS on 2026-11-11; recheck before implementing, since the site may change):

| Element | ic.byu.edu | Notes |
|---|---|---|
| Logo | `https://ic.byu.edu/wp-content/uploads/2023/08/Vector.svg`: a single-path icon, `viewBox="0 0 218 191"`, `fill="black"`, no text | The "INTERNATIONAL CINEMA" wordmark next to it is live text, not part of the SVG |
| Wordmark font | `HW Cigars Trial SemiBold`, 36px, centered (site title) | Trial license, so not used; an OFL substitute is chosen instead (below) |
| Heading and body font | `PPNeueMontreal-Bold` (`._H1`, `._Body`), line-height 1.1 (H1) / 1.2 (body). Book, Medium, Italic and SemiBold Italic cuts are also loaded | Commercial, so not used; an OFL substitute is chosen instead (below) |
| Other fonts | `Gerstner-Programm-Medium`, `HW-Cigars-Trial-Medium` (declared), Overpass (Google Fonts, inline styles only) | Gerstner is commercial; Overpass is OFL |
| Palette | Monochrome: text `#000000`, background `#ffffff`, secondary text `#7a7a7a` (the most common color on the page), further grays `#595959` `#999999` `#adadad`, panels `#eeeeee`, dark `#0a0a0a`, overlay `rgba(51,51,51,…)` | No brand accent color in the theme CSS. A few yellows/oranges appear once each in inline content, not as theme colors |
| Voice | All-caps section labels ("PLAYING THIS WEEK"), film stills as the main imagery, a lot of white space | |
| Favicon | `…/uploads/2023/09/cropped-favicon-{32x32,180x180,192x192}.png` | |

**Scope (decided): only patron-facing surfaces carry IC branding.** Patrons see only what reaches the projector: the **output window**. Everything operator- or staff-facing uses Y-video's own UI conventions (`ui-ux-standards.md`).

| Surface | Audience | Styling |
|---|---|---|
| Output window (`/ic/output/`) on the booth, and the same page in the production preview | patrons | **IC-branded** (below) |
| Booth control screen (`/ic/`, `/ic/program/<id>/`, `/ic/status/`, sync indicators) | projectionist | Y-video UI conventions, **dark mode** (below) |
| Production `/ic/` preview controller | IC editors | Same dark-mode controller as the booth, so rehearsing on production feels identical |
| `/ic/manage/` pages, header entry | IC editors | Standard Y-video pages. The header entry is the plain label "International Cinema" |

**How the plan applies IC branding to the output window:**
- **Assets are vendored, never hot-linked**, because the booth must work offline (§8.3):
  - `core/static/ic/img/ic-logo.svg`: IC's `Vector.svg`, **committed to the repo (approved)**, with `fill="black"` changed to `fill="currentColor"` so it renders white on the black output. It's included inline via a template partial so it inherits `color`.
  - Fonts: open-licensed (OFL) families only, self-hosted as `woff2` in `core/static/ic/fonts/` with their `OFL.txt`. They're downloaded once from Google Fonts (or the family's repo) and committed. Pages never hot-link Google Fonts.
- **Design tokens** live in `core/static/css/ic-output.css` as custom properties (`--ic-black: #000`, `--ic-white: #fff`, `--ic-gray-1: #7a7a7a`, `--ic-gray-2: #595959`, `--ic-font-wordmark`, `--ic-font-display`). Output templates use only these tokens. Add a test like `test_js_constant_parity.py` asserting that no other hex colors appear in `ic-output.css`.
- **Lockup:** logo + "INTERNATIONAL CINEMA" in the wordmark font, as in the ic.byu.edu header. One template partial (`core/templates/core/ic/partials/lockup.html`).
- **What patrons see:**
  - Pure black at all times except during film.
  - When idle, before the first item and between items: the lockup in white, centered, at a modest size. It fades in after 1 s of idle so quick transitions stay black.
  - Optional pre-show slate (e.g., "PLAYING THIS WEEK" + program name) in the display font, all caps, white on black, when a program starts with no announcement item.
  - VTT subtitle cues: keep the projection-tuned cue style from §6.3, in the IC display font so the on-screen type matches the brand.
  - No player chrome, cursor (`cursor: none`), focus rings or browser UI, ever.
- **Font choice (open/free only):** ic.byu.edu's own faces (PP Neue Montreal, HW Cigars Trial, Gerstner Programm) are commercial or trial-licensed, so we use the closest open alternatives instead:
  - **Display/subtitles** (stands in for PP Neue Montreal, a neo-grotesk): candidates are Hanken Grotesk, Inter and Overpass (all OFL, all on Google Fonts; Overpass is already used on ic.byu.edu).
  - **Wordmark** (stands in for HW Cigars): pick the OFL face closest to the "INTERNATIONAL CINEMA" title on ic.byu.edu.
  - In PR 4, make a one-page side-by-side comparison against screenshots of ic.byu.edu, get IC's sign-off, and commit only the chosen weights. Subtitles need a face that stays legible at projection size, so check the candidates with real VTT cues on the projector during the spike or rehearsal.
  - Font stacks end in `system-ui, sans-serif`, but because the files are committed and served locally there's no runtime fallback in practice.

**Booth dark mode (operator-facing, not IC-branded):**
- The controller uses Y-video's components, layout and interaction patterns from `ui-ux-standards.md`, with a **dark theme**. A booth is dark during showings, and a bright operator monitor spills light toward the audience and wrecks the projectionist's night vision.
- Implementation: a `core/static/css/dark.css` token layer (custom properties for background, surface, text, muted text, border, focus, success/warning/danger), applied by the IC base template with `data-theme="dark"` and `color-scheme: dark`. Build the controller only on these variables so the theme stays consistent. It's also a starting point if Y-video ever adds dark mode globally.
- Constraints for the booth:
  - Near-black background (not pure `#000`, so panels can be distinguished), low-glare text.
  - Status colors (✅ ⚠️ ❌ ⏸ in the sync indicators) stay readable at low brightness: muted green/amber/red with text labels, never color alone.
  - A big, unambiguous emergency **BLANK** button.
  - Contrast meets WCAG AA in dark mode. Add an automated contrast check over the token pairs.
- The favicon and window titles on booth pages are Y-video's. Only the output window's title is "INTERNATIONAL CINEMA", which is harmless since it's fullscreen.

**Licensing:**
- Fonts: OFL only, committed with their license files (decision). ic.byu.edu's commercial and trial faces are not used.
- Logo: approved for committing to the repo.

## 7. Sync (PRs 6 and 7)

### 7.1 Transport and triggers
**All bytes and rows travel one way, by one code path: the booth downloads from production and verifies.** And **every connection starts on the booth**: production never connects to it, and **sync is pull-only**: production can't make the booth sync. What varies is *what starts* a run:

| Trigger | Started by | When |
|---|---|---|
| **Automatic** (`auto`) | the booth sync agent's check (§7.3) | within ~30 s of a change on production; 15 min after a run that didn't end cleanly; at agent start; the weekly re-hash |
| **Sync now** (`force_pull`) | the projectionist, on the booth controller | immediately |

The booth's agent **checks** production every `IC_CHECK_INTERVAL_SECONDS` (30) with one cheap, token-authenticated GET whose answer is production's current manifest hash (cached for 15 s). `check_parent` is the one place that decides whether a run is due.

There is no push from production, by decision. An IC editor who fixes something waits for the booth's next check (≤ ~45 s), and production's dashboard shows when the booth matches. Reasons:
- **One verification path, one writer.** Checksums, fingerprints, code-version checks and rollback live in the booth's sync agent only.
- **No inbound surface on the booth**, and no second protocol to keep consistent. An earlier design had production call a signed agent endpoint on the booth; a later one had the booth collect "push requests" and post their progress. Both duplicated the run's lifecycle on production, and the second could leave a push "running" forever when a report was lost.
- Personal instances (§11), which production can't reach, work exactly like the booth.

Everything resumes after interruption (HTTP Range downloads into `.part` files), so a forced run picks up partial downloads left by an earlier run.

### 7.2 Production: bundle and API (`core/ic_sync/`)
`build_bundle()` covers `ICProgram.syncable()` and the closure of what it references:

| Model | Rows |
|---|---|
| `ICProgram`, `ICProgramItem` | syncable programs |
| `Content` | items' content |
| `Playlist` | those contents' playlists |
| `Resource` | content resources |
| `ResourceFile` | the projection master and `content.resource_file` (metadata only; the stream file's bytes are not shipped) |
| `AnnotationSet`, `Track`, the 7 annotation subclasses, `BlurAnchorPoint` | complete sets, including inactive rows, so the `prev`/`next` undo FKs stay valid |
| `Subtitle` | tracks on those resources (bytes shipped as files, below) |
| `Language` | referenced |
| `User` | **stubs** for FK integrity (`Playlist.owner` is non-null): pk, first/last name, unusable password, `is_active=False`. No netid, email, username or courses. |

- Serialized with `django.core.serializers` and **original PKs**, so ids inside the player JSON match.
- Manifest:
  ```json
  {
    "format": 1,
    "generated_at": "2026-11-11T20:00:00Z",
    "git_sha": "59dc541…",
    "migrations": {"core": "00NN_…"},
    "fixture": {"sha256": "…", "size": 123456},
    "files": [
      {"kind": "projection", "resource_file_id": 812, "size": 10737418240, "xxh64": "…", "available_from_parent": true},
      {"kind": "subtitle",   "subtitle_id": 77,         "size": 48211,       "xxh64": "…"}
    ],
    "fingerprints": {"<content_id>": "sha256(canonical player json)"},
    "programs": [{"id": 4, "start_date": "…", "end_date": "…", "item_ids": [9, 10]}]
  }
  ```
  "Canonical" means `json.dumps(get_player_json(include_subtitles=True), sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, encoded as UTF-8. Both sides run identical code, so this is deterministic.
- **Manifest hash:** sha256 of the canonical manifest minus `generated_at`. It's served as the manifest's `ETag`, so polls with `If-None-Match` get a cheap 304, and it's the single value that tells whether a booth matches production.
- Endpoints, all bearer-token authenticated (hashed token on an `ICInstance` row, constant-time compare), `@login_not_required`, CSRF-exempt, and listed in the permission-test exemptions with reasons:
  - `GET /ic/sync/check/`: `{"manifest_hash"}`, the agent's frequent check (§7.3). Also stamps `ICInstance.last_seen_at`, which is how production knows the booth is alive
  - `GET /ic/sync/manifest/` (supports `If-None-Match`)
  - `GET /ic/sync/fixture/`
  - `GET /ic/sync/files/projection/<resource_file_id>/`: Range-capable via `_file_response()` with a larger range cap for this bulk path; 403 unless the file is in the current manifest
  - `GET /ic/sync/files/subtitle/<subtitle_id>/`
  - `POST /ic/sync/status/`: the booth reports run results (§7.3)
- `ICInstance(name, token_hash, last_seen_at, last_status JSONField, last_applied_manifest_hash, created_at)`. Superusers manage it in Django admin (issuing a token is an infrastructure task, not an IC editing task). There's one row today. The token is the only credential, and it's used only by the booth to call production.

### 7.3 Booth sync agent
A long-running management command, `manage.py ic_sync_agent`, runs as its own systemd user service in the booth image (§8.4). It's the only part of the booth that talks to production.

Agent loop:
- At startup: mark stale `running` rows failed and queue one `auto` run. The agent usually restarts because the booth just deployed, which is when a run refused for `code_mismatch` can succeed; if nothing changed, it costs one 304.
- Every 30 s (`IC_CHECK_INTERVAL_SECONDS`): **check** production (`GET /ic/sync/check/`) and record its manifest hash on `ICBoothState` (for the controller's "production has changes" badge). If no run is queued or running, queue an `auto` run when:
  - production's hash differs from the applied one and differs from the last run's (a change nobody has tried): at once;
  - the booth is behind, or the last run didn't end `succeeded`/`up_to_date`: 15 minutes after that run finished (a run that never saw a manifest waits too, so a broken endpoint isn't asked every 30 s);
  - otherwise, released masters exist, it's between 03:00 and 11:00, and the last full re-hash was over a week ago: an `auto` run with `full_rehash`.
  - If the check fails, record the error (the badge says "Can't reach production") and carry on. Playback never depends on it.
- Every 2 s: pick up queued `ICSyncRun` rows ("Sync now" first) and run them. The check keeps running during a run (from the run's heartbeat), which keeps production's "last contact" fresh; it queues nothing while a run is active.
- Every second or so: write a heartbeat file so the kiosk can tell whether the agent is alive.
- One run at a time, enforced by an `fcntl` lock file (same pattern as `process_legacy_migration_jobs`). Stale `running` rows are marked `failed (agent restarted)` at startup.

Booth-local model, written only by the agent (except creating a `queued` row for "Sync now", §7.6):
```python
class ICSyncRun(models.Model):
    id = UUIDField(primary_key=True)
    trigger = CharField(choices=["auto", "force_pull"])
    requested_by = CharField(blank=True)    # "booth operator", "agent start", or blank
    options = JSONField(default=dict)       # {"full_rehash": bool}
    status = CharField(choices=["queued", "running", "succeeded", "partial", "failed", "up_to_date"])
    phase = CharField(blank=True)           # contacting / downloading / importing / verifying / downloading_upcoming / reporting
    bytes_done = BigIntegerField(default=0); bytes_total = BigIntegerField(default=0)
    manifest_hash = CharField(blank=True)   # what this run fetched
    report = JSONField(default=dict)        # per-program results, errors, free disk
    requested_at / started_at / finished_at
```

Steps of a run:
1. **Manifest.** Fetch `GET /ic/sync/manifest/`.
   - `auto` runs send `If-None-Match: <applied manifest hash>`, **but only if the previous run succeeded or was up to date**. A 304 ends the run as `up_to_date` within milliseconds. After a partial or failed run, the next run fetches the manifest again, so files that failed to download are retried rather than hidden behind a 304.
   - "Sync now" runs always fetch and continue, even if nothing changed. A forced run is the way to say "prove the booth matches production right now."
   - If `git_sha` or `migrations` differ from the booth's own, stop with `failed: code_mismatch` and say which side is behind. The booth deploy timer tracks production's branch outside `IC_PLAYBACK_HOURS`, so this resolves within minutes of a daytime deploy, or after the evening's showings.
2. **Files needed now.** All subtitle files, plus the masters of programs that have already started. Subtitles always come first because the player JSON embeds their text, so fingerprints (step 3) can't match without them; they're small. For each:
   - Missing, wrong size, or not in `ICLocalFile`: Range-download to `<dest>.part`, resuming from its size; verify xxh64; atomic rename; record in `ICLocalFile(kind, ref_id, path, size, xxh64, verified_at)`.
   - With `full_rehash` (a "Sync now" option, plus the weekly re-hash of released masters): re-hash every local file even if it was verified before. This catches disk corruption. A file that fails is removed from `ICLocalFile` at once, so it never counts as verified (or playable) again, even if it can't be replaced. It takes minutes for large masters, and the progress bar shows it.
   - Downloads are always rate-capped (15 MB/s, about 11 minutes per 10 GB), so a sync can't cause stutter whenever it runs.
3. **Database.** Applied as soon as step 2's files are verified, **without waiting for masters of later programs**, so a fix to tonight's annotations never waits behind next month's 20 GB master. If a file from step 2 that production can still send failed, the booth keeps its previous rows (and reports why), because applying would make a film that plays today unplayable. Then:
   - Download the fixture and check its sha256.
   - In one `transaction.atomic()`: delete all mirrored-model rows (children first), deserialize, `obj.save()`. Deserialized saves use `raw=True`, which bypasses `ResourceFile.save()` checksum/barcode logic (good). **Check that `stamp_previous_owner_on_annotation_sets` and any other signal handlers respect `raw`.**
   - Rewrite `ResourceFile.file` and `Subtitle.subtitles_file` names to the booth-local paths recorded in `ICLocalFile`.
   - **Verify:** recompute each content's fingerprint and compare with the manifest. On any mismatch, roll back, keep the previous mirror, and fail with the content ids.
   - `auto` runs skip the import if the fixture hash equals the one already applied (the manifest hash is still recorded as applied). "Sync now" runs always re-import and re-verify.
   - This step never waits for playback (§7.4).
4. **Files for later programs.** Masters of programs that haven't started yet, as in step 2. Their rows are already in place; until a master is verified, its items show "not on this booth yet".
5. **Prune** local files no longer referenced by the current manifest *and* not referenced by any program active in the last 3 days. **Released masters** (`available_from_parent: false`) follow the stricter rule in §5.7 (30 days after their last program, with a warning 7 days ahead), because the booth holds the only copy. The grace period also guarantees that a master replaced mid-showing stays on disk until the film using it has finished (§7.4).
6. **Report.** Finish the `ICSyncRun` row and POST the report to production (`POST /ic/sync/status/`). If that fails, the next run's report replaces it; production shows the latest report it has.

### 7.4 Never change the film that's playing
- The output fetches an item's player data once, when the item loads, and keeps it in memory. The player data carries the item's **media URL**, so the annotations and the file they're timed against always arrive as one pair.
- On the booth that URL is `/ic/file/<resource_file_id>/<xxh64>/`. It names one verified file and is served from `ICLocalFile`, not from the mirrored `ResourceFile` rows, so **no import can change the bytes behind it**. A replaced master has a new checksum, so it gets a new URL, and prune's grace period keeps the old file on disk until well after the showing. The response is `Cache-Control: immutable`.
- Files are written to `.part` and atomically renamed, and a replaced master gets a new checksum-based filename, so a file in use is never modified in place.
- So **a sync can apply at any time**, including mid-film. New data takes effect the next time an item is loaded (the next showing, or "Reopen and resume", which loads the new pair).
- Playback state isn't tracked at all: downloads are always rate-capped, the sync agent runs at low I/O priority, and the weekly re-hash runs only in the morning.
- Code updates are the other way the kiosk could be disturbed (a redeploy restarts the container that serves the film). The booth's deploy timer skips `IC_PLAYBACK_HOURS` (§8.4); if the controller ever looks stale after a redeploy, the projectionist reloads the page.

An earlier design addressed media by item (`/ic/item/<id>/media/`, resolved on every range request) and protected the playing film by deferring the DB swap until playback stopped, with an "Apply even while playing" override. That didn't hold: a pause counted as stopped, and the override let the next range request of a playing film resolve to a *different* master (or a 404 if the new one hadn't downloaded). File-addressed URLs make the invariant structural, and they removed the `waiting_for_playback` state and the override.

### 7.5 Change-triggered pulls
- The agent's 30-second check (§7.3) compares production's manifest hash with the applied one and queues an `auto` run for a change nobody has tried.
- Production does nothing special: no signals, no notify job. The manifest hash is cached for 15 s, so the check costs at most one bundle build per 15 s, and an editing session's burst of saves is coalesced naturally. The booth sees a change within about 30–45 s.
- A missed check (network blip) only delays the next one by 30 s. Runs that didn't finish cleanly are retried 15 minutes later by the same rule.

### 7.6 Force pull from the booth (with indicator)
- The controller page has a **Sync now** button, with the option "Re-check every file (slow)" → `full_rehash`.
- `POST /ic/sync/run/` (kiosk port only, CSRF-protected) creates a `queued` `ICSyncRun(trigger="force_pull", requested_by="booth operator")` and returns its id. If a "Sync now" run is already queued, the same id is returned instead of a second run (with `full_rehash` added if this press asked for it). If an automatic run is in progress, the force run is queued behind it, and the indicator says so.
- If the agent's heartbeat is older than 30 s, the POST returns an immediate error: **"Sync agent isn't running; restart it with `systemctl --user restart ic-sync-agent`"**. A request nobody will pick up never sits silently in the queue.
- The indicator polls `GET /ic/sync/run/<id>/` every second with HTMX (§7.8).

### 7.7 What "success" means, and the indicators
A run ends in one of:

| Status | Meaning |
|---|---|
| ✅ **Succeeded** | Manifest fetched; every file for every syncable program present and xxh64-verified; fixture applied; **every fingerprint equals production's**; report delivered. The indicator shows the applied manifest hash and time. |
| ✅ **Up to date** | (automatic runs only) production returned 304; nothing to do. |
| ⚠️ **Partial** | Some programs aren't playable yet. Each one is listed with its reason, e.g. "Amélie: master still transcoding on production", "Kung Fu Panda: checksum failed". If a master for a program already showing couldn't be fetched, the booth kept its previous rows and says so (§7.3 step 3). It's retried 15 minutes later. |
| ❌ **Failed** | Nothing changed on the booth, which keeps playing its previous verified data. The reason is specific: `code_mismatch` (which side is behind), `fingerprint_mismatch` (which contents), `checksum_failed` (which file), production unreachable, disk full (free vs. needed). |

The "Sync now" indicator shows a phase stepper:

```txt
Contacting production ✓ → Downloading current films 3/5 files · 4.1 of 10.0 GB → Importing → Verifying → Downloading upcoming films → Reporting
```

Persistent status, outside of any forced run:
- **Booth controller badge**, always visible:
  - "Synced ✅ 14:02, matches production"
  - "⚠️ Last sync failed 14:10 (production unreachable); playing from local copy synced 13:50"
  - "Production has changes the booth doesn't have yet"
  - "⚠️ Can't reach production since 14:20; playing from the local copy"

  It reads the manifest hash the agent's check last saw (`ICBoothState.parent_manifest_hash`, §7.3), so it can say whether production has newer changes without the kiosk ever calling production. Offline, it shows the last-known state without implying anything is wrong with what's on disk.
- **Production dashboard** (§7.8): whether the booth matches production (applied manifest hash vs. current), last successful sync, last run status, and when the booth last checked in (with a warning when it hasn't for 2 minutes).

Audit: every `ICSyncRun` records its trigger and requester, listed on the booth's `/ic/status/` page; production keeps each booth's latest report.

### 7.8 Readiness dashboard on production
`/ic/manage/` (IC editors) shows a programs × instance matrix:
- Production readiness (§4.6)
- Booth: files verified ✅ · fingerprint matches **current** production fingerprint ✅ · last successful sync · last check-in
- **"Changed since last booth sync"**: production fingerprint ≠ booth-reported fingerprint. This catches "I fixed a mute this morning; is it on the booth?" The answer turns ✅ within about a minute of the fix, without anyone doing anything.
- Recent sync history per instance: trigger, who, status, duration, applied manifest hash.

### 7.9 Tests
- **Round-trip:** factories → `build_bundle()` → import into a clean DB (a second SQLite alias in tests, or flush the mirrored tables) → all fingerprints equal and `get_player_json()` deep-equal.
- Resume a partial download; a corrupt file fails xxh64, is retried, and fails the run with the file named.
- A fingerprint mismatch rolls back and the previous mirror is intact.
- `code_mismatch` refuses to import and reports which side is behind.
- An automatic run with an unchanged manifest → 304 → `up_to_date`. After a partial run, the next run fetches again and retries the missing files. A "Sync now" run with an unchanged manifest still re-imports and re-verifies. `full_rehash` re-hashes verified files.
- Ordering: rows are applied before masters for later programs download; a failed download for a later program still applies the rows; a failed download for a program showing now keeps the previous rows.
- Media: the booth's media URL names one file and keeps serving the same bytes after an import replaces the item's master; player data carries the URL.
- Queueing: "Sync now" while an automatic run is running → queued behind it; two presses → one run; stale agent heartbeat → immediate error on "Sync now"; the agent syncs at startup.
- Check: a new manifest hash → one run (not again while queued); a failed or partial run → retried 15 minutes later, at once if production changes; a run that never saw a manifest waits too; the weekly re-hash only in the morning and only with released masters; production unreachable → error recorded, nothing queued.
- Production offers no push route, and its dashboard says so.
- A file that fails a re-hash is never trusted again.
- Subtitle checksums are computed once per file version, not on every manifest build.
- Prune grace period; released masters are never pruned early. A missing released master → `master_lost` → readiness ❌.
- Master release: not released until both the SD transcode succeeds *and* the booth reports a freshly hashed match; a re-check inside the job blocks a race with a replaced master; the row survives release; re-upload restores the same pk.
- Token auth for production's sync API: missing, wrong or revoked tokens are rejected.
- **PII regression:** the fixture contains no netid, email, username or course data.

## 8. Booth instance mode (PR 5)

### 8.1 URL surface: one urlconf, bound to localhost
The booth's web container runs the kiosk urlconf only. The surface reachable at all is decided by structure (which routes exist), not by a check someone has to remember. This follows the "structural rather than remembered" principle in `core/permissions.py`.

| Bound to | `ROOT_URLCONF` | Routes |
|---|---|---|
| `127.0.0.1:<port>` only | `yvideo.urls_ic` | control, output, player-data, local files, `/ic/status/`, sync badge, `POST /ic/sync/run/` + `GET /ic/sync/run/<id>/` (force pull, §7.6), static |

- The booth has **no inbound network surface**: the kiosk is bound to localhost, and the sync agent only makes outbound requests (§7.1).
- The urlconf has no admin, auth routes, editor or API. Tests assert that representative core names (`video_editor`, `admin:index`, `playlists`, `stream_file`) and production's IC routes (`ic_manage_dashboard`, `ic_sync_manifest`, `ic_item_media`) don't resolve in it.
- **What "read-only" means on the booth:** no HTTP request can change mirrored data. The only HTTP write is "Sync now". It can only create a queued `ICSyncRun`, and the run can only fetch data from `IC_PARENT_URL` and verify it.

### 8.2 Middleware and settings when `IS_IC_INSTANCE`
- Remove `LoginRequiredMiddleware`, `SpoofUserMiddleware`, `mozilla_django_oidc.middleware.SessionRefresh`.
- Keep `AuthenticationMiddleware` (so `request.user` is `AnonymousUser` for context processors).
- Add `ICReadOnlyMiddleware`: rejects every method except GET/HEAD with 405, except "Sync now". It's a safety net behind the urlconf.
- `POST /ic/sync/run/` keeps Django's CSRF protection (the controller page includes the token).
- Drop `mozilla_django_oidc` from `INSTALLED_APPS` and `AUTHENTICATION_BACKENDS` if that's clean. Otherwise leave them unused.
- `ALLOWED_HOSTS = ["localhost", "127.0.0.1"]`. **Userless works only because nothing off-box can reach the kiosk.**
- Booth settings: `IC_PARENT_URL`, `IC_SYNC_TOKEN`, `IC_CHECK_INTERVAL_SECONDS = 30`, `IC_POLL_INTERVAL_SECONDS = 900`.
- `DEBUG = False`. Run `collectstatic` at container start (already in `entrypoint.sh`), so all static assets are local.

### 8.3 Offline guarantees (decision #1)
Checklist, enforced by tests where possible:
- [ ] IC templates load no external URLs. Add a template test that renders the IC pages and asserts no `http(s)://` in `src`/`href`, excluding none.
- [ ] IC fonts (committed OFL `woff2` files) and the IC logo are served from local static files, never from ic.byu.edu or Google Fonts.
- [ ] No URL/YouTube content can be in a program (readiness error).
- [ ] Projection masters and VTT files are local and verified before the program counts as ready.
- [ ] Player data comes from the local DB; no request leaves the booth during playback.
- [ ] Sync failures (DNS, TLS, 5xx, timeouts), for any trigger including "Sync now", are logged and shown in the indicators. They never touch the current mirror or files.
- [ ] The controller's "production has newer changes" badge (§7.8) comes from the agent's last check, so the kiosk never calls production, and an offline booth just shows "Can't reach production".
- [ ] Manual test: unplug the network, reboot the booth, and run a full program.

### 8.4 Booth OS setup (`deploy/ic/`)
- Same Podman/quadlet deploy as production (`DEPLOY.md`), with `BRANCH` pinned to production's branch, so git SHAs match (§7.3). The deploy timer polls GitHub every minute like production's, except during `IC_PLAYBACK_HOURS` in `.env` (local `start-end`, default `12-24`; `0-0` disables the guard), so a redeploy never restarts the kiosk during a showing. Redeploys are fast and code changes rare; worst case, the projectionist reloads the controller.
- Two quadlet units from the same image, sharing the data and projection volumes (SQLite WAL on one host):
  - the main unit (kiosk): `PublishPort=127.0.0.1:<port>:8000`
  - `ic-sync-agent`: runs `manage.py ic_sync_agent` (§7.3), `Restart=always`, with `Nice=10` and `IOSchedulingClass=best-effort` (priority 7) so sync I/O yields to playback (downloads are also rate-capped)
- Firewall: nothing to open. The booth makes only outbound HTTPS requests to production.
- Make the entrypoint role-aware: only one unit runs `migrate`, so the containers don't race. The kiosk unit does it, and the agent starts `After=` it.
- `deploy/ic/ic-kiosk.service.in` (systemd user unit in the booth's graphical session): `google-chrome-stable <flags chosen in the spike, §6.1> --app=http://localhost:<port>/ic/ --start-fullscreen --autoplay-policy=no-user-gesture-required --noerrdialogs --disable-session-crashed-bubble --disable-features=Translate --user-data-dir=$HOME/.config/ic-kiosk`. Plus Ubuntu auto-login for the booth account, and screen blanking, screensaver and notifications disabled (`gsettings` commands in the doc).
- `deploy/ic/chrome-policy.json` installed to `/etc/opt/chrome/policies/managed/ic.json`: `WindowManagementAllowedForUrls`, `AutoplayAllowlist`, `DefaultNotificationsSetting: 2`, `TranslateEnabled: false`, `PasswordManagerEnabled: false`, `BrowserSignin: 0`, `RestoreOnStartup` pointing at the control URL.
- Install `intel-media-va-driver-non-free` and `vainfo`. Add a `vainfo` and `chrome://gpu` check to the post-update rehearsal checklist, because VA-API behavior is what a Chrome or driver update is most likely to break.
- **Chrome updates:** Google's apt repo updates Chrome with the OS. Keep that (security, and "latest Chrome" is the supported target), but don't let it happen mid-showing: configure `unattended-upgrades` to apply in a weekday-morning window outside showtimes, and add "play the next program's first minute after a Chrome update" to the rehearsal checklist. If an update ever breaks playback, `apt-mark hold google-chrome-stable` is the emergency brake.
- Display setup notes: screen 2 = output, projector mirrors screen 2, and audio is routed to the booth sound system from Chrome's output device. Use the display server chosen in the spike (§6.1).
- These replace `IC_player/IC_Player.sh`.
- New doc: `DEPLOY_IC.md`.

## 9. Migrating the legacy IC library (PR 8)

`manage.py import_ic_legacy <dir> --mapping mapping.csv`:
- Walks `*.icf`, resolves `annotation` and `.ic/<video>`.
- The mapping CSV gives `icf_path → content_id` (content must be in an `is_ic` playlist).
- Builds an `AnnotationSet` per film:
  - `skip` → `SkipAnnotation`; `mute` → `MuteAnnotation`
  - `blank` → `BlankAnnotation(type="k")`; whole-screen `blur` → `BlankAnnotation(type="#")`
  - `censor` → `BlurAnnotation` + `BlurAnchorPoint`s
  - Positions with 2 values inherit width/height from the previous key, as in `icPositionsToBlurAnchorPoints()`.
- `--with-video`: uploads `.ic/<video>` as the projection master and runs §5. **This is the safest default for legacy films, because that m4v is exactly what the legacy annotations were timed against.** Using a better master later means re-checking timings (the readiness `timing_verified` flag exists for this).
- **Parity test:** the Python converter and `AnnotationPlayer.parseICLegacyAnnotations()` produce equivalent annotations for shared fixtures (`IC_player/resources/example/KungFuPandamv_IC.json`, `kung-test/`), in the style of `test_js_constant_parity.py`.

## 10. PR plan

| # | Scope | Depends on |
|---|---|---|
| 0 | **Spike** on the booth: two-window fullscreen + smooth playback of the largest real master in Chrome (§6.1). Throwaway HTML, no Django | — |
| 1 | Settings, IC editor group/`is_ic_editor`, `is_projection_file` + probe fields + `derived_from`, `Playlist.is_ic`, `ICProgram`/`ICProgramItem`, readiness, admin registrations, Content guard | — |
| 2 | IC management UI (`/ic/manage/`): program CRUD with item ordering, `is_ic` toggle in playlist settings, resources page, dashboard shell, permission decorators | 1 |
| 3 | Chunked upload, `MediaJob` worker (low priority), probe/faststart/SD transcode, Dockerfile ffmpeg, master restore-on-re-upload | 1, 2 |
| 4 | `/ic/` controller (dark mode) + output (IC-branded: tokens, lockup, committed logo, self-hosted OFL fonts chosen with IC), e2e | 0, 1 |
| 5 | Booth instance mode: `urls_ic`, middleware/settings, offline checks, `deploy/ic/`, `DEPLOY_IC.md` | 4 |
| 6 | Sync pipeline: bundle, manifest/ETag, production sync API, `ICInstance`, `ic_sync_agent` + `ICSyncRun`, verification, file-addressed media, status reports, dashboard sync columns | 2, 3, 5 |
| 7 | Master release (§5.7: `ICInstanceFile`, `release_master` job, destructive-prune rules, weekly re-hash, `master_lost`) and triggers and indicators: the agent's 30-second check (change runs, retries, weekly re-hash), "Sync now" (controller button + live indicator + persistent badge) | 6 |
| 8 | Legacy importer + parity test | 1 (3 for `--with-video`) |
| 9 | Parallel run, `MANUAL_TESTING.md` IC section, remove `IC_player/` | all |

The spike (0) can start immediately. After PR 1, PRs 2, 4 and 8 can proceed in parallel, and PR 3 follows PR 2. PR 7 is split from PR 6 so the data path can be reviewed and tested on its own before the trigger surfaces land.

### Rollout
1. Provision the booth (PR 5) and point it at production with its token. Confirm an automatic sync and a "Sync now" both show ✅ before the parallel run.
2. **Parallel run for 1–2 weeks:** IC staff prepare programs in Y-video, the booth syncs, and the projectionist rehearses each program on the booth. The Electron player stays installed as a fallback.
3. Cut over. After one clean month, delete `IC_player/` (Electron app, symlinks, `scripts/`), its npm/eslint references, and `IC_Player.sh`, and update `README.md`.

### Manual test script (add to `MANUAL_TESTING.md`)
Upload a master → transcode finishes → edit annotations → readiness ✅ → booth syncs → dashboard shows the booth matching → rehearse → open output on screen 2 → LOOP announcement → advance to film → skip/mute/blur/blank fire → VTT cues are readable on the projector → emergency blank → close the output mid-film and resume → unplug the network mid-film → reboot offline and replay → edit an annotation on prod during playback and confirm the booth applies the change without disturbing the film, and the change shows the next time the item loads.

Sync triggers (each must end in the indicator state shown):
- Edit an annotation on prod, wait ~1 min → change run → booth badge ✅ "matches production".
- **Force pull** with nothing changed → ✅ Succeeded (re-verified), not "up to date".
- **Force pull** with "Re-check every file" → progress shows re-hashing → ✅.
- Booth powered off → after ~2 min production's dashboard shows ⚠️ "hasn't checked in recently"; power it on → it syncs at once → dashboard ✅ matches.
- Stop `ic-sync-agent`, then force pull → immediate ❌ "sync agent isn't running".
- Merge a commit during `IC_PLAYBACK_HOURS` → the booth doesn't redeploy until they end.
- Force pull with the network unplugged → ❌ "production unreachable"; playback unaffected.
- Corrupt a byte in a local master, force pull with re-check → the file is re-downloaded → ✅.
- An annotation fix (or a replaced master) synced during a film → applied straight away; the current film is uninterrupted.
- A new program for next month plus an annotation fix for tonight → tonight's fix is applied before next month's master finishes downloading.

## 11. Future: personal IC staff instances (decision #7)

The booth design already makes these mostly configuration. A personal instance is an `ICInstance` row plus a token. Because every connection starts on the instance (§7.1), it gets the same checks and change runs as the booth, with no inbound connectivity. Two things stand between this and "trivial":
- **Packaging:** Podman + quadlet + systemd is Linux-specific. Something trivial for staff (likely macOS) probably means a single `podman machine`/Docker Desktop compose file, or a small installer script. Revisit once the booth is stable.
- **Media policy:** personal machines should probably sync the **derived stream file** instead of the master (smaller, and less sensitive if a laptop is lost). Add an `ICInstance.profile = booth | preview` field to choose which file kind the manifest lists, and count only `booth` instances toward releasing a master (§5.7). The fingerprint guarantees still hold, because annotations are timed against both files identically (§5.3).

## 12. Risks and open questions
| Risk | Mitigation |
|---|---|
| Two-window fullscreen on Ubuntu Wayland + Chrome | Spike PR 0 tests native Wayland, XWayland and Xorg together with VA-API; two-instance fallback with a localhost relay |
| Booth can't smoothly decode uncapped masters (UHD 630: no AV1; HEVC only with working VA-API in Chrome) | Spike with the largest real master; codec rules in readiness; `IC_BOOTH_VERIFIED_MAX` warning; rehearsal before opening night |
| **Booth holds the only copy of each master** after release | Release only after a fresh hash on the booth; weekly re-hash; `master_lost` alerts; stricter booth prune with a 7-day warning; re-upload restores; IC keeps the originals |
| Transcode load on production | `Nice=19`, idle I/O class, `CPUWeight=10`, `-threads 2`, one job at a time (§5.4) |
| Disk usage of uncapped masters (~10 GB typical, rarely >20 GB) | Production: masters are released after the SD transcode and booth verification (§5.7), with a free-space check before upload. Booth: prune rules plus free space shown on the dashboard |
| Chrome auto-update changes behavior | Update window outside showtimes; rehearsal checklist; `apt-mark hold` as the emergency brake |
| IC editors are a new, powerful role (uploads, programs) | Group membership is granted only by superusers; reversion history on IC models; projection uploads still require resource access |
| Legacy annotations timed against old m4v | Import with `--with-video`; `timing_verified` gate when swapping masters |
| Code version skew between prod and booth | `git_sha` + migration check; booth tracks prod's branch |
| Disk space on booth | Prune with a grace period; dashboard shows booth free space (included in the status POST) |
| Signal handlers firing during raw import | Audit and test (§7.3) |
| ic.byu.edu redesign drifts from our copy | Branding is limited to the output window, one token file and one lockup partial, so a refresh is a one-file change; re-read the site before PR 4 |
| Sync agent silently dead | Kiosk heartbeat file: "Sync now" fails immediately with the restart command; production's dashboard flags a booth that hasn't checked in for 2 minutes |
| Load from the booth's 30-second check | One token-authenticated GET; the manifest hash is cached for 15 s and subtitle checksums per file version, so at most one bundle build per 15 s |
| A sync changing a film mid-showing | File-addressed media URLs paired with the player data; old files kept through prune's grace period (§7.4) |

Open questions: none outstanding. Earlier questions and their answers are in the decisions table (§1).

## 13. Out of scope, but found while planning
`deploy/apache-vhost-example.conf` serves `/media/` with `Require all granted`, so uploaded files can be fetched by path without a `ResourceFileKey`. That's tracked in **#531**. This plan doesn't depend on its fix and avoids making it worse by keeping masters in `IC_PROJECTION_ROOT`, outside `MEDIA_ROOT`.

## 14. Implementation notes

Where the code differs from the plan above, and why:

- **A separate `ic` Django app** holds the IC models, views, jobs and sync. Core gained only
  the shared fields (`ResourceFile.is_projection_file` and probe metadata, `derived_from`,
  `master_released_at`; `Playlist.is_ic`; `User.is_ic_editor`) and the guards that keep
  masters out of content.
- **One storage, routed by name.** `ResourceFile.file` uses `yvideo.storage.ResourceFileStorage`,
  which stores names under `ic_projection/` in `IC_PROJECTION_ROOT` and everything else in
  `MEDIA_ROOT`. Masters have the same name on production and the booth, so the booth never
  rewrites file paths after an import.
- **Checksums are computed by the ingest job**, not incrementally during the upload: xxhash
  state can't be saved between requests, and hashing a ~10 GB file in the worker takes
  seconds. Faststart remuxing happens before hashing, so the checksum always describes the
  stored bytes.
- **Booth state** lives in `ICSyncRun`, `ICLocalFile` and a singleton `ICBoothState` (applied
  manifest and fixture hashes, last success, last full re-hash). The sync agent's
  heartbeat is a small file in `IC_STATE_DIR`, so the kiosk can read it without the
  database.
- **Fingerprints** hash `get_player_json()` after sorting its lists by id, because some of
  those lists come from querysets without an `ORDER BY`. Any real difference in a field still
  changes the fingerprint. The fixture loses sub-millisecond precision on `created_at` /
  `updated_at` (Django's JSON serializer), which are not part of the player data.
- **The booth kiosk serves its own static files** (there is no Apache on the booth), from
  `collectstatic`'s output, through a view that only exists in the kiosk urlconf.
- **One image, three roles**, chosen by `YVIDEO_ROLE` in `deploy/entrypoint.sh`: `web` (site or
  kiosk; the only role that migrates), `media-worker`, `ic-sync-agent`.
  `deploy.sh` passes the commit into the image as `YVIDEO_GIT_SHA`, because the build
  context excludes `.git` and the booth compares versions before applying data.
- **ffmpeg before 5.1** lacks `-fps_mode`; the transcode falls back to the equivalent
  `-vsync passthrough`. The container's Debian ffmpeg is newer.
- **Fonts:** the output uses Overpass (OFL; already used on ic.byu.edu), self-hosted, for
  the wordmark, slates and subtitles. Swapping in another OFL face after IC's review means
  replacing `ic/static/ic/fonts/` and the two `--ic-font-*` tokens in `ic-output.css`.
- **Personal instances** (§11) are not built; the manifest always lists projection masters.
- **`IC_player/` is still in the tree.** Remove it after the parallel run and cutover
  (DEPLOY_IC.md, "Moving off the Electron IC Player").
- **Local demo:** `manage.py seed_ic_demo` (after `seed_demo_data`) builds an IC playlist and
  a program for today from `demo_media/`.
- **Outbound-only sync (revised after review).** The first implementation had production
  call a signed agent endpoint on the booth (an `ic-agent-web` container and urlconf, HMAC
  secrets on both machines, a firewall rule, change-notification signals and an
  `ic_notify` job). It was replaced by the booth's 30-second check (§7.1, §7.3);
  migration `ic/0002_outbound_only_sync` drops the agent fields.
- **Pull-only sync (revised after the second review).** Push-to-booth briefly survived as
  `ICSyncRequest`, a request the booth collected and reported progress on. It duplicated
  the run lifecycle on production and could stay "running" forever when a report was lost.
  It was removed along with the 15-minute poll (the check's retry rule covers it), the
  playback heartbeat (downloads are always capped) and `ICInstance.profile`; see migration
  `ic/0003_pull_only_sync`. The booth's deploy timer skips `IC_PLAYBACK_HOURS`.
- **File-addressed media (revised after review).** The booth's media URL used to name the
  item and was resolved on every range request, so a sync applied during a pause (or with
  "Apply even while playing") could switch a playing film to another file. It now names
  the file and its checksum (§7.4), and the playback wait and its override are gone.
- **Subtitles before rows.** `get_player_json()` embeds VTT text, so subtitle files are
  always fetched before the rows are applied; only masters for later programs wait.
- **Ingest replaces a master safely.** The new bytes move into place under their own
  checksum name before the transaction, the old row is removed with a queryset delete
  (`ResourceFile.delete()` would remove its bytes immediately), and the old bytes are
  deleted only after the commit. If anything fails, production keeps the previous master
  and the upload's bytes go back to the `.part` file.
