# IC Mode: replacing the IC Player with Y-video (#246)

Status: **draft for review**

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
| Transport | Booth and prod can reach each other. Data always flows by **booth download + verification** (§7.1) |
| Sync triggers | Normally poll + event-triggered pull. **Force push from production and force pull from the booth**, both with a live success indicator (§7.5–7.8) |
| Booth writes | Booth is read-only: no HTTP request can change mirrored data. The only HTTP writes are requests to start a sync (§8.1) |
| Booth users | Booth is userless: no login, no admin |
| Displays | Two monitors; the projector mirrors the output monitor |
| Editing file | **Transcoded from the projection master** |
| Subtitles | Burned-in still supported; **VTT tracks preferred** from now on |
| Booth count | One. Personal instances only if deployment is trivial |
| Booth platform | Ubuntu (Wayland session) + latest Google Chrome on a Dell with **Intel UHD Graphics 630** (Comet Lake, `i915`) (§6.1) |
| File specs | Projection master = maximum available resolution/bitrate (no cap); streaming/editing copy = SD |
| Master retention | **Production deletes its copy of a master as soon as the SD transcode is complete and the booth's copy is downloaded and checksum-verified.** The booth then holds the only copy (§5.7) |
| Force push | All IC editors and superusers |
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
Sync API: manifest, fixture, files  ◄── booth pulls ──   /ic/output/   (projector monitor)
Status/results                      ◄── run reports ──   ic_sync_agent (poll 15 min + queued runs)
Event notify / "Push to booth now"  ── signed trigger ─► agent port (LAN): start run, read progress
```

### Invariants
1. The booth never receives annotation JSON, only rows. It renders player JSON with the same code.
2. Before a synced program counts as playable, the booth's `sha256(canonical player JSON)` for each content must equal production's. Otherwise the import is rolled back and the previous good mirror stays in place.
3. Every file the booth plays is on local disk and its xxh64 has been verified against production's checksum.
4. A sync never changes the film that's playing, and by default waits for playback to stop before applying anything (§7.4).
5. Every trigger (poll, event, force pull, force push) runs the same booth-side pipeline. Forcing changes *when* and *how thoroughly* it checks, never *whether* it verifies.

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
| `/ic/manage/` | Dashboard: programs (active, upcoming, past) × readiness × booth sync state, Push to booth now (§7.7, §7.9); media job queue with progress |
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
Add a `MediaJob(kind, status, payload, log, created_at, started_at, finished_at)` table and `manage.py process_media_jobs`. Kinds: `probe`, `faststart`, `transcode`, `cleanup_uploads`, and `ic_notify` (event-triggered booth pulls, §7.5).

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
2. Every instance with `profile = booth` (today: the one booth) has reported, in a sync status POST, that it holds the file **verified by hashing the downloaded bytes** (xxh64 equal to `ResourceFile.checksum`). A cached "already verified" flag isn't enough for the first confirmation.

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
| `ic:item_player_data` | `/ic/item/<item_id>/player-data/` | **GET**, calls `Content.get_player_json()` and adds `fingerprint`; subtitles filtered per `item.subtitles` |
| `ic:item_media` | `/ic/item/<item_id>/media/` | Range stream via the shared `_file_response()` |
| `ic:status` | `/ic/status/` | booth: sync log + readiness (read-only) |

- On **production**, the item media is the *derived SD file* (bandwidth-friendly preview), and access requires `is_ic_editor`.
- On the **booth**, the item media is the *local projection master*. Access requires `ICProgram.is_active()` (or `?rehearse=1` for upcoming programs; see §6.4), and no user.
- `player_data` becomes GET here (the existing endpoint is POST with CSRF only because it lives in the authenticated app). That keeps the booth GET-only.
- Add `ic_*` decorators in `core/permissions.py` and register the views in `test_view_permissions.py`. On the booth urlconf, views are exempt with a documented reason ("booth is userless and bound to localhost").

### 6.3 Frontend files
- `core/templates/core/ic/base.html` is a standalone base: no Google Fonts, no header/footer, no external links, no spoof banner or what's-new. Follow `ui-ux-standards.md` for the controller.
- Styling (§6.6): `core/static/css/ic-output.css` holds the IC brand tokens for the output window, and `core/static/css/dark.css` holds the dark-mode tokens for the controller. The Electron player's CSS and assets are not ported.
- `core/static/js/ic-output.js`:
  - Constructs `AnnotationPlayer({editorMode:false, subtitleSidebar:false, allowFastPlayback:false, disabledControls:[…all…]})`.
  - On `loadItem`: `setVideoSource(mediaUrl)`, then `loadData(playerData)`. Captions are rendered as on-screen cues when `item.subtitles == "vtt"`.
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
**All bytes and rows travel one way, by one code path: the booth downloads from production and verifies.** What varies is *what starts* a sync:

| Trigger | Started by | When | Normal or manual |
|---|---|---|---|
| **Poll** | booth sync agent | every 15 min, and at boot | normal (safety net) |
| **Event** | production, after a relevant change | ~1 min after edits settle (debounced) | normal (fast path) |
| **Force pull** | operator, "Sync now" on the booth controller | immediately | manual |
| **Force push** | IC editor, "Push to booth now" on the production dashboard | immediately | manual |

Force push is implemented as **production commanding the booth to run a sync now, then watching it to completion.** Production doesn't write files or rows onto the booth itself. Reasons:
- **One verification path.** Checksums, fingerprints, code-version checks, rollback and the playback guard live in one place (the booth's sync agent). A second, production-side writer would need all of it duplicated, and a bug in either path would be exactly the kind of annotation corruption this project is meant to remove.
- **The booth is the only side that knows whether it's playing.** It decides whether applying now is safe (§7.4).
- **Same success indicator everywhere.** Production shows the booth's own run report, so "success" means the same thing no matter where you clicked.
- Personal instances (§11), which production can't reach, still work with poll and force pull. The push button is simply hidden for them.

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
  - `GET /ic/sync/manifest/` (supports `If-None-Match`)
  - `GET /ic/sync/fixture/`
  - `GET /ic/sync/files/projection/<resource_file_id>/`: Range-capable via `_file_response()` with a larger range cap for this bulk path; 403 unless the file is in the current manifest
  - `GET /ic/sync/files/subtitle/<subtitle_id>/`
  - `POST /ic/sync/status/`: the booth reports run results (§7.3)
- `ICInstance(name, token_hash, agent_url (blank = pull-only, e.g. personal instances), last_seen_at, last_status JSONField, last_applied_manifest_hash, last_notified_manifest_hash, created_at)`. Superusers manage it in Django admin (issuing a token is an infrastructure task, not an IC editing task). There's one row today.
- `ICSyncRequest(instance, requested_by, request_id, manifest_hash_at_request, options, booth_run_id, status, report, created_at, finished_at)` records force pushes (§7.7).

### 7.3 Booth sync agent
A long-running management command, `manage.py ic_sync_agent`, runs as its own systemd user service in the booth image (§8.4). It replaces the earlier timer design, because force requests need to start within seconds.

Agent loop:
- Every 2 s: pick up queued `ICSyncRun` rows (created by force pull, force push or event triggers) and run them.
- Every 15 min: create and run a `poll` run.
- Every 10 s: write a heartbeat (timestamp + agent version) so the web side can tell whether the agent is alive.
- One run at a time, enforced by an `fcntl` lock file (same pattern as `process_legacy_migration_jobs`). Stale `running` rows are marked `failed (agent restarted)` at startup.

Booth-local model, written only by the agent (except creating a `queued` row, §8.1):
```python
class ICSyncRun(models.Model):
    id = UUIDField(primary_key=True)
    trigger = CharField(choices=["poll", "event", "force_pull", "force_push"])
    requested_by = CharField(blank=True)    # "booth operator" or the production user's name
    request_id = UUIDField(null=True, unique=True)   # idempotency key from production
    options = JSONField(default=dict)       # {"full_rehash": bool, "apply_during_playback": bool}
    status = CharField(choices=["queued", "running", "waiting_for_playback",
                                "succeeded", "partial", "failed", "up_to_date"])
    phase = CharField(blank=True)           # contacting / downloading / importing / verifying / reporting
    bytes_done = BigIntegerField(default=0); bytes_total = BigIntegerField(default=0)
    manifest_hash = CharField(blank=True)   # what this run applied
    report = JSONField(default=dict)        # per-program results, errors, free disk
    requested_at / started_at / finished_at
```

Steps of a run:
1. **Manifest.** Fetch `GET /ic/sync/manifest/`.
   - `poll` and `event` runs send `If-None-Match: <applied manifest hash>`. A 304 ends the run as `up_to_date` within milliseconds.
   - `force_*` runs always fetch and continue, even if nothing changed. A forced run is the way to say "prove the booth matches production right now."
   - If `git_sha` or `migrations` differ from the booth's own, stop with `failed: code_mismatch` and say which side is behind. The booth deploy timer tracks production's branch, so this resolves within minutes of a deploy.
2. **Files.** Soonest-starting program first. For each manifest file:
   - Missing, wrong size, or not in `ICLocalFile`: Range-download to `<dest>.part`, resuming from its size; verify xxh64; atomic rename; record in `ICLocalFile(kind, ref_id, path, size, xxh64, verified_at)`.
   - With `full_rehash` (a force-only option): re-hash every local file even if it was verified before. This catches disk corruption. It takes minutes for large masters, and the progress bar shows it.
   - Downloads and re-hashing are paced while the output is playing (§7.4).
3. **Database.** Once all files for at least one program are verified, and §7.4 allows:
   - Download the fixture and check its sha256.
   - In one `transaction.atomic()`: delete all mirrored-model rows (children first), deserialize, `obj.save()`. Deserialized saves use `raw=True`, which bypasses `ResourceFile.save()` checksum/barcode logic (good). **Check that `stamp_previous_owner_on_annotation_sets` and any other signal handlers respect `raw`.**
   - Rewrite `ResourceFile.file` and `Subtitle.subtitles_file` names to the booth-local paths recorded in `ICLocalFile`.
   - **Verify:** recompute each content's fingerprint and compare with the manifest. On any mismatch, roll back, keep the previous mirror, and fail with the content ids.
   - `poll`/`event` runs skip this step if the fixture hash equals the one already applied. `force_*` runs always re-import and re-verify.
4. **Prune** local files no longer referenced by the current manifest *and* not referenced by any program active in the last 3 days. **Released masters** (`available_from_parent: false`) follow the stricter rule in §5.7 (30 days after their last program, with a warning 7 days ahead), because the booth holds the only copy.
5. **Report.** Finish the `ICSyncRun` row and POST the report to production (`POST /ic/sync/status/`, which includes the `run_id` and `request_id` so production can match it to a push).

### 7.4 Never change the film that's playing
- The output window's heartbeat reaches the server via a tiny GET (`/ic/heartbeat/?state=playing`). It's stored in a tmpfile timestamp, never the DB.
- The item currently loaded in the output keeps its media URL and player JSON in memory. **No sync can change a film that's already playing**; new data takes effect the next time an item is loaded.
- Files are written to `.part` and atomically renamed, and a replaced master gets a new checksum-based filename, so a file in use is never modified in place.
- While playback was active in the last 2 minutes:
  - Downloads and re-hashing are throttled so disk and network I/O can't cause stutter. This applies to every trigger, forced or not.
  - The DB swap waits (`status = waiting_for_playback`, shown in the indicators) until playback stops, the program ends, or the operator returns to the program list.
  - Forced runs offer **"Apply even while playing"** (unchecked by default, with a confirmation). It's safe for the current item because of the in-memory rule above. It exists for "we fixed a mute in the second film of a double feature during the first one."

### 7.5 Event-triggered pulls (production)
- Signals (`post_save`/`post_delete`) on the mirrored model types call `transaction.on_commit(schedule_ic_notify)`.
- `schedule_ic_notify` upserts a single pending `MediaJob(kind="ic_notify")` with `not_before = now + 60 s`, pushing it later on each new change. An editing session therefore produces one notification after it settles, not one per annotation drag.
- The job recomputes the manifest hash (cheap: a few programs). If it differs from `ICInstance.last_notified_manifest_hash`, it sends a signed trigger (§7.7) with `trigger = "event"` to every instance that has an `agent_url`.
- Notification failures are logged and shown on the dashboard. The 15-minute poll covers them, so a missed notification only delays a sync.
- Readiness changes that affect sync (a transcode finishing, a master being replaced) also schedule a notify.

### 7.6 Force pull from the booth (with indicator)
- The controller page has a **Sync now** button. Options: "Re-check every file (slow)" → `full_rehash`, and "Apply even while playing" (§7.4).
- `POST /ic/sync/run/` (kiosk port only, CSRF-protected) creates a `queued` `ICSyncRun(trigger="force_pull", requested_by="booth operator")` and returns its id. If a forced run is already queued, the same id is returned instead of a second run. If a poll or event run is in progress, the force run is queued behind it, and the indicator says so.
- If the agent's heartbeat is older than 30 s, the POST returns an immediate error: **"Sync agent isn't running; restart it with `systemctl --user restart ic-sync-agent`"**. A request nobody will pick up never sits silently in the queue.
- The indicator polls `GET /ic/sync/run/<id>/` every second with HTMX (§7.8).

### 7.7 Force push from production (with indicator)
On `/ic/manage/` each instance with an `agent_url` gets a **Push to booth now** button (IC editors), with the same two options.

1. Production creates `ICSyncRequest(instance, requested_by, request_id=uuid4, manifest_hash_at_request, options)`. Then, with a 5 s timeout, it sends `POST <agent_url>/ic/agent/sync/` with a signed body `{request_id, requested_by, options, manifest_hash}`.
2. The booth's agent endpoint (§8.1) verifies the signature, creates or returns the `ICSyncRun` for that `request_id`, and answers `202 {run_id}`. Repeating the same `request_id` (a retry, or a replayed request) returns the same run and never creates a second one.
3. The production page polls its own `GET /ic/manage/sync-requests/<id>/` every 2 s. That view asks the booth (`GET <agent_url>/ic/agent/sync/<run_id>/`, signed, 3 s timeout) for live progress and renders it. Once the run finishes, the final report is stored on `ICSyncRequest`, so the result stays visible after the page is closed.
4. If the booth becomes unreachable mid-run, the indicator shows "Lost contact with the booth at hh:mm; it may still be syncing." It switches to the final result when the booth's report arrives via `POST /ic/sync/status/` (matched by `request_id`).

Failure is reported immediately and specifically, for example:
- "Booth unreachable: connection refused at booth.example:8443" (booth off, wrong address, firewall)
- "Booth rejected the request: bad signature" (secret mismatch between production and booth)
- "Booth sync agent isn't running" (the booth's agent heartbeat is stale)

#### Request signing
- Shared secret per instance: on production in `secret_settings.IC_INSTANCE_AGENT_SECRETS = {"<instance name>": "..."}`, and on the booth in `secret_settings.IC_AGENT_SECRET`. It's kept out of the DB so a DB dump doesn't leak it, and it's separate from the booth→production bearer token.
- Headers: `X-IC-Timestamp` and `X-IC-Signature = hex(HMAC-SHA256(secret, f"{timestamp}\n{method}\n{path}\n{sha256(body)}"))`. The booth rejects timestamps more than 60 s off and compares signatures in constant time. `request_id` idempotency makes replays harmless.
- The agent surface can only *start a sync against the real production* or *read status*. A forged-but-valid request couldn't inject data, because the data always comes from `IC_PARENT_URL` and is verified.
- The LAN hop is plain HTTP by default (signed, with no secrets in responses). Put TLS in front of the agent port if campus network policy requires it.

### 7.8 What "success" means, and the indicators
A run ends in one of:

| Status | Meaning |
|---|---|
| ✅ **Succeeded** | Manifest fetched; every file for every syncable program present and xxh64-verified; fixture applied; **every fingerprint equals production's**; report delivered. The indicator shows the applied manifest hash and time. |
| ✅ **Up to date** | (poll/event only) production returned 304; nothing to do. |
| ⚠️ **Partial** | Data applied and verified, but some programs aren't playable yet. Each one is listed with its reason, e.g. "Amélie: master still transcoding on production", "Kung Fu Panda: downloading 61%". |
| ⏸ **Waiting for playback** | Files are ready, and the DB swap is waiting for playback to stop (§7.4). |
| ❌ **Failed** | Nothing changed on the booth, which keeps playing its previous verified data. The reason is specific: `code_mismatch` (which side is behind), `fingerprint_mismatch` (which contents), `checksum_failed` (which file), production unreachable, disk full (free vs. needed). |

Both force indicators show a phase stepper:

```txt
Contacting production ✓ → Downloading 3/5 files · 4.1 of 10.0 GB · ETA 6 min → Importing → Verifying → Reporting
```

For a push, the production page adds **"Booth now matches production"** or **"Booth matches the state at the time you pushed; 1 change since (12:41, Amélie)"**. It compares the applied manifest hash with production's current one.

Persistent status, outside of any forced run:
- **Booth controller badge**, always visible:
  - "Synced ✅ 14:02, matches production"
  - "⚠️ Last sync failed 14:10 (production unreachable); playing from local copy synced 13:50"
  - "Production has newer changes; syncing…"

  The controller checks production's manifest ETag every minute when online, so it can say whether production has newer changes. Offline, it shows the last-known state without implying anything is wrong with what's on disk.
- **Production dashboard** (§7.9): last successful sync, last run status, agent heartbeat age, and the event notification status per instance.

Audit: every `ICSyncRun` records its trigger and requester, and every `ICSyncRequest` records the production user. Both are listed on the dashboard and on the booth's `/ic/status/` page.

### 7.9 Readiness dashboard on production
`/ic/manage/` (IC editors) shows a programs × instance matrix:
- Production readiness (§4.6)
- Booth: files verified ✅ · fingerprint matches **current** production fingerprint ✅ · last successful sync · agent alive
- **"Changed since last booth sync"**: production fingerprint ≠ booth-reported fingerprint. This catches "I fixed a mute this morning; is it on the booth?" A **Push to booth now** button sits right next to it.
- Recent sync history per instance: trigger, who, status, duration, applied manifest hash.

### 7.10 Tests
- **Round-trip:** factories → `build_bundle()` → import into a clean DB (a second SQLite alias in tests, or flush the mirrored tables) → all fingerprints equal and `get_player_json()` deep-equal.
- Resume a partial download; a corrupt file fails xxh64, is retried, and fails the run with the file named.
- A fingerprint mismatch rolls back and the previous mirror is intact.
- `code_mismatch` refuses to import and reports which side is behind.
- Poll/event with an unchanged manifest → 304 → `up_to_date`. A force run with an unchanged manifest still re-imports and re-verifies. `full_rehash` re-hashes verified files.
- Playback: throttling for all triggers; DB swap deferred by default; "apply even while playing" swaps without touching the in-memory item.
- Queueing: force while a poll is running → queued behind it; two force requests → one run; stale agent heartbeat → immediate error.
- Signing: missing/bad signature, timestamp outside 60 s, and a replayed `request_id` all fail to create a new run. Agent routes don't resolve in the kiosk urlconf, and kiosk routes don't resolve in the agent urlconf.
- Production push view: booth unreachable → immediate failure; booth vanishes mid-run → "lost contact", later resolved by the status POST.
- Event debounce: 50 annotation saves within a minute → one notify job → one trigger. An unchanged manifest hash → no trigger.
- Prune grace period; released masters are never pruned early. A missing released master → `master_lost` → readiness ❌.
- Master release: not released until both the SD transcode succeeds *and* the booth reports a freshly hashed match; a re-check inside the job blocks a race with a replaced master; the row survives release; re-upload restores the same pk.
- Token auth for production's sync API: missing, wrong or revoked tokens are rejected.
- **PII regression:** the fixture contains no netid, email, username or course data.

## 8. Booth instance mode (PR 5)

### 8.1 URL surface: two urlconfs, two containers
The booth runs the same image in two web roles, each with its own urlconf. That way the surface reachable from the LAN is decided by structure (which routes exist), not by a check someone has to remember. This follows the "structural rather than remembered" principle in `core/permissions.py`.

| Role | Bound to | `ROOT_URLCONF` | Routes |
|---|---|---|---|
| **kiosk** (`IC_ROLE=kiosk`) | `127.0.0.1:<port>` only | `yvideo.urls_ic` | control, output, player-data, media, heartbeat, `/ic/status/`, `POST /ic/sync/run/` + `GET /ic/sync/run/<id>/` (force pull, §7.6), static |
| **agent** (`IC_ROLE=agent`) | booth LAN IP `:<agent port>`, firewalled to production's IP | `yvideo.urls_ic_agent` | `POST /ic/agent/sync/`, `GET /ic/agent/sync/<run_id>/`, `GET /ic/agent/status/`. Nothing else: no static files, no player, no media |

- Neither urlconf has the admin, auth routes, the editor or the API. Tests assert that representative core names (`video_editor`, `admin:index`, `playlists`, `stream_file`) don't resolve in either, that agent routes don't resolve in the kiosk urlconf, and the reverse.
- **What "read-only" means on the booth:** no HTTP request can change mirrored data. The only HTTP writes are the two "start a sync" endpoints. Each can only create a queued `ICSyncRun`, and the run can only fetch data from `IC_PARENT_URL` and verify it.

### 8.2 Middleware and settings when `IS_IC_INSTANCE`
- Remove `LoginRequiredMiddleware`, `SpoofUserMiddleware`, `mozilla_django_oidc.middleware.SessionRefresh`.
- Keep `AuthenticationMiddleware` (so `request.user` is `AnonymousUser` for context processors).
- Add `ICReadOnlyMiddleware`: rejects every method except GET/HEAD with 405, except the allowlisted sync-start routes for the current role. It's a safety net behind the urlconfs.
- Kiosk: `POST /ic/sync/run/` keeps Django's CSRF protection (the controller page includes the token). Agent: HMAC signature verification (§7.7) in a decorator, and CSRF-exempt.
- Drop `mozilla_django_oidc` from `INSTALLED_APPS` and `AUTHENTICATION_BACKENDS` if that's clean. Otherwise leave them unused.
- Kiosk `ALLOWED_HOSTS = ["localhost", "127.0.0.1"]`. **Userless works only because nothing off-box can reach the kiosk.** Agent `ALLOWED_HOSTS = [<booth LAN hostname/IP>]`.
- Booth settings: `IC_ROLE`, `IC_PARENT_URL`, `IC_SYNC_TOKEN`, `IC_AGENT_SECRET`, `IC_POLL_INTERVAL_SECONDS = 900`.
- `DEBUG = False`. Run `collectstatic` at container start (already in `entrypoint.sh`), so all static assets are local.

### 8.3 Offline guarantees (decision #1)
Checklist, enforced by tests where possible:
- [ ] IC templates load no external URLs. Add a template test that renders the IC pages and asserts no `http(s)://` in `src`/`href`, excluding none.
- [ ] IC fonts (committed OFL `woff2` files) and the IC logo are served from local static files, never from ic.byu.edu or Google Fonts.
- [ ] No URL/YouTube content can be in a program (readiness error).
- [ ] Projection masters and VTT files are local and verified before the program counts as ready.
- [ ] Player data comes from the local DB; no request leaves the booth during playback.
- [ ] Sync failures (DNS, TLS, 5xx, timeouts), for any trigger including force pull and push, are logged and shown in the indicators. They never touch the current mirror or files.
- [ ] The controller's "production has newer changes" check (§7.8) fails quietly when offline and never blocks the UI.
- [ ] Manual test: unplug the network, reboot the booth, and run a full program.

### 8.4 Booth OS setup (`deploy/ic/`)
- Same Podman/quadlet deploy as production (`DEPLOY.md`), with `BRANCH` pinned to production's branch, so git SHAs match (§7.3).
- Three quadlet units from the same image, all sharing the data and projection volumes (SQLite WAL on one host):
  - `ic-kiosk-web`: `IC_ROLE=kiosk`, `PublishPort=127.0.0.1:<port>:8000`
  - `ic-agent-web`: `IC_ROLE=agent`, `PublishPort=<booth LAN IP>:<agent port>:8000`, `WORKERS=1`
  - `ic-sync-agent`: runs `manage.py ic_sync_agent` (§7.3), `Restart=always`, with `Nice=10` and `IOSchedulingClass=best-effort` (priority 7) so sync I/O yields to playback
- Firewall (`ufw`): allow the agent port only from production's IP. Everything else stays closed inbound.
- Make the entrypoint role-aware: only one unit runs `migrate`, so the three containers don't race. The kiosk unit does it, and the others start `After=` it.
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
| 6 | Sync pipeline: bundle, manifest/ETag, production sync API, `ICInstance`, `ic_sync_agent` with poll + `ICSyncRun`, verification, playback guard, status reports, dashboard sync columns | 2, 3, 5 |
| 7 | Master release (§5.7: `ICInstanceFile`, `release_master` job, destructive-prune rules, weekly re-hash, `master_lost`) and triggers and indicators: force pull (controller "Sync now" + live indicator + persistent badge), agent role/urlconf/container + HMAC signing, force push (`ICSyncRequest`, dashboard button + live indicator), event notify (signals + debounced `ic_notify` job) | 6 |
| 8 | Legacy importer + parity test | 1 (3 for `--with-video`) |
| 9 | Parallel run, `MANUAL_TESTING.md` IC section, remove `IC_player/` | all |

The spike (0) can start immediately. After PR 1, PRs 2, 4 and 8 can proceed in parallel, and PR 3 follows PR 2. PR 7 is split from PR 6 so the data path can be reviewed and tested on its own before the trigger surfaces (which add an inbound LAN port) land.

### Rollout
1. Provision the booth (PR 5), point it at production with its token, set the agent secret on both sides, and open the agent port to production. Confirm a force push and a force pull both show ✅ before the parallel run.
2. **Parallel run for 1–2 weeks:** IC staff prepare programs in Y-video, the booth syncs, and the projectionist rehearses each program on the booth. The Electron player stays installed as a fallback.
3. Cut over. After one clean month, delete `IC_player/` (Electron app, symlinks, `scripts/`), its npm/eslint references, and `IC_Player.sh`, and update `README.md`.

### Manual test script (add to `MANUAL_TESTING.md`)
Upload a master → transcode finishes → edit annotations → readiness ✅ → booth syncs → dashboard shows the booth matching → rehearse → open output on screen 2 → LOOP announcement → advance to film → skip/mute/blur/blank fire → VTT cues are readable on the projector → emergency blank → close the output mid-film and resume → unplug the network mid-film → reboot offline and replay → edit an annotation on prod during playback and confirm the booth throttles and waits, then applies afterwards.

Sync triggers (each must end in the indicator state shown):
- Edit an annotation on prod, wait ~1 min → event pull → booth badge ✅ "matches production".
- **Force pull** with nothing changed → ✅ Succeeded (re-verified), not "up to date".
- **Force pull** with "Re-check every file" → progress shows re-hashing → ✅.
- **Force push** from the dashboard → live phases → ✅ "Booth now matches production".
- Force push, then edit during the push → ✅ with "1 change since".
- Force push with the booth powered off → immediate ❌ "Booth unreachable".
- Force push with a wrong agent secret → ❌ "bad signature".
- Stop `ic-sync-agent`, then force pull and force push → immediate ❌ "sync agent isn't running".
- Force pull with the network unplugged → ❌ "production unreachable"; playback unaffected.
- Corrupt a byte in a local master, force pull with re-check → the file is re-downloaded → ✅.
- Force push during a film without "apply while playing" → ⏸ waiting → ✅ after stop. With it → applied, and the current film is uninterrupted.

## 11. Future: personal IC staff instances (decision #7)

The booth design already makes these mostly configuration. A personal instance is an `ICInstance` row plus a token with no `agent_url`. It uses polling and force pull, and it needs no inbound connectivity. Force push and event notifications are hidden or skipped for it, and its agent web container isn't deployed. Two things stand between this and "trivial":
- **Packaging:** Podman + quadlet + systemd is Linux-specific. Something trivial for staff (likely macOS) probably means a single `podman machine`/Docker Desktop compose file, or a small installer script. Revisit once the booth is stable.
- **Media policy:** personal machines should probably sync the **derived stream file** instead of the master (smaller, and less sensitive if a laptop is lost). Add `ICInstance.profile = booth | preview` to choose which file kind the manifest lists. Only `booth` instances count toward releasing a master (§5.7). The fingerprint guarantees still hold, because annotations are timed against both files identically (§5.3).

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
| New inbound LAN surface on the booth (agent port) | Separate container and urlconf exposing three routes; firewall to production's IP; HMAC + timestamp + idempotent `request_id`; the routes can only start a verified pull from the real production |
| Sync agent silently dead | Heartbeat; force buttons fail immediately with the restart command; dashboard shows heartbeat age |
| Event storm from heavy editing | 60 s debounced single notify job; manifest-hash comparison skips no-op changes |
| Production web worker blocked calling the booth | Short timeouts (5 s trigger, 3 s progress); the final result also arrives via the booth's status POST |

Open questions: none outstanding. Earlier questions and their answers are in the decisions table (§1).

## 13. Out of scope, but found while planning
`deploy/apache-vhost-example.conf` serves `/media/` with `Require all granted`, so uploaded files can be fetched by path without a `ResourceFileKey`. That's tracked in **#531**. This plan doesn't depend on its fix and avoids making it worse by keeping masters in `IC_PROJECTION_ROOT`, outside `MEDIA_ROOT`.
