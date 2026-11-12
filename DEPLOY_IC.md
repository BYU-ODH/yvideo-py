# Deploying International Cinema (IC) mode

International Cinema shows films from a booth computer running its own copy of Y-video.
Production is where IC editors prepare everything; the booth is a userless, read-only
mirror that plays from its own disk, so a network outage cannot affect a showing.
The design is in [IC_MODE_PLAN.md](IC_MODE_PLAN.md); general deployment is in
[DEPLOY.md](DEPLOY.md), which this file builds on.

```txt
PRODUCTION                                          BOOTH (Ubuntu + Google Chrome)
<app>.service           web, /ic/manage, sync API    <app>.service              kiosk (127.0.0.1 only)
<app>-media-worker      ingest, SD transcode,        <app>-ic-sync-agent        checks, pulls, verifies, applies
                        master release               <app>-ic-kiosk (Chrome)    controller + output
```

Every connection goes from the booth to production; the booth needs no inbound ports and
production holds no booth credentials. The booth's sync agent checks production every
30 seconds (for changes, and for pushes from IC editors) and reports back what it did.

Both machines must run the same commit: a booth refuses production's data when the code
versions differ (it reports `code_mismatch` until its deploy timer catches up). Pin the
booth's `.env` `BRANCH` to production's branch.

## Production

### 1. Settings

In `.env`:

```bash
IC_MEDIA_WORKER=1
# Optional; defaults to <checkout>/ic_projection. Never under media/ (#531).
IC_PROJECTION_ROOT=/srv/yvideo-prod/ic_projection
```

In `yvideo/secret_settings.py`:

```python
FILE_UPLOAD_TEMP_DIR = "var/upload_tmp"
IC_BOOTH_VIDEO_CODECS = ["h264"]  # from the booth spike; add "hevc" only if verified
IC_BOOTH_VERIFIED_MAX = {"height": 1080, "frame_rate": 30, "bitrate": 40_000_000}
```

Plan disk space for masters: about 10 GB each, rarely over 20 GB, plus about 1.2× that
during upload processing. Production deletes each master once the booth holds a
verified copy and the SD file exists, so steady-state use is small.

### 2. Deploy

```bash
bash deploy/install_quadlet.sh
systemctl --user restart <app>.service <app>-media-worker.service
```

From then on the deploy timer rebuilds and restarts both. The image now includes
ffmpeg, and `deploy.sh` stamps it with the commit (`YVIDEO_GIT_SHA`).

The media worker runs at the lowest CPU and I/O priority (`Nice=19`,
`IOSchedulingClass=idle`, `CPUWeight=10`), and ffmpeg is limited to two threads, so a
transcode does not slow the site. Check that `CPUWeight` took effect:

```bash
systemctl --user show -p CPUWeight <app>-media-worker.service
```

### 3. Register the booth

1. In the Django admin, add an **IC instance** named `booth` with profile *Booth*.
   Saving shows the booth's **sync token once**; copy it.
2. Add IC staff to the **IC Editors** group (Admin → Users). They then see
   *International Cinema* in the sidebar.

To rotate the token, use the admin action *Issue a new sync token*.

### Apache

Masters upload in 16 MB `PUT`s to `/ic/manage/uploads/…/chunk/`, and booths download in
up to 256 MB ranges from `/ic/sync/files/…`. The example vhost needs no changes; if you
set `LimitRequestBody`, keep it above 16 MB for `/ic/`.

## Booth

### 1. Machine

- Ubuntu with Google Chrome stable from Google's apt repository.
- Intel graphics: `sudo apt install intel-media-va-driver-non-free vainfo`, then check
  that `vainfo` lists `VAProfileH264High` (and `VAProfileHEVCMain` if HEVC will be used).
- One user account that both owns the checkout (the deploy user from DEPLOY.md) and
  logs in automatically to the desktop, so the kiosk unit runs in its graphical session.
- Screen blanking, screensaver and notifications off:
  `gsettings set org.gnome.desktop.session idle-delay 0`,
  `gsettings set org.gnome.desktop.screensaver lock-enabled false`,
  `gsettings set org.gnome.desktop.notifications show-banners false`.
- Displays: screen 1 is the operator's; screen 2 is the output, mirrored to the
  projector. Route Chrome's audio to the booth sound system.
- `unattended-upgrades` restricted to a weekday-morning window, away from showtimes
  (Chrome updates come through apt). After any Chrome or driver update, rehearse a
  minute of the next program before it is shown; `sudo apt-mark hold google-chrome-stable`
  is the emergency brake if an update breaks playback.

### 2. The spike: do this before the first showing

The booth's hardware and Chrome decide two things nothing else can test
(IC_MODE_PLAN.md section 6.1). With the largest real master IC expects:

1. **Hardware decode.** Play it and check `chrome://gpu` (Video Decode: Hardware
   accelerated) and `chrome://media-internals` (the decoder is a VA-API one, not
   `FFmpegVideoDecoder`; dropped frames near 0). Try, in order: Chrome's defaults;
   `--enable-features=VaapiVideoDecoder,VaapiVideoDecodeLinuxGL,VaapiIgnoreDriverChecks`;
   that plus `--ozone-platform=x11`; and an "Ubuntu on Xorg" login.
2. **Output placement.** With the same flags, open a program and press *Open output*:
   the output should land on screen 2 and go fullscreen (or after one click on it).
3. Record what worked: the flags in `.env` `IC_CHROME_FLAGS`; the highest resolution,
   frame rate and bitrate that played smoothly in production's `IC_BOOTH_VERIFIED_MAX`;
   and `"hevc"` in `IC_BOOTH_VIDEO_CODECS` only if HEVC decoded in hardware. The UHD 630
   has no AV1 decoder, so AV1 masters are always refused.

### 3. Settings

`.env`:

```bash
IC_BOOTH=1
IC_CHROME_FLAGS="<flags from the spike>"
BRANCH=<production's branch>
```

`yvideo/secret_settings.py`:

```python
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
IS_IC_INSTANCE = True
IC_PARENT_URL = "https://<production host>"
IC_SYNC_TOKEN = "<token from production's admin>"
```

The booth has no OIDC, no admin and no users; the OIDC and API settings can stay empty.

### 4. Install

```bash
bash deploy/install_quadlet.sh
bash deploy/install_deploy_timer.sh
systemctl --user start <app>.service <app>-ic-sync-agent.service
bash deploy/ic/install_kiosk.sh
```

`install_kiosk.sh` installs `<app>-ic-kiosk.service` (Chrome in `--app` mode on the
controller) and prints a command to run once with `sudo`, which installs the Chrome
policy (pop-ups, autoplay, fullscreen and window placement for the kiosk; no sign-in,
translation or notifications):

```bash
sudo install -D -m 644 var/ic/chrome-policy.json /etc/opt/chrome/policies/managed/ic.json
```

The booth needs no inbound firewall openings. The kiosk listens on 127.0.0.1 only; being
userless is safe only because nothing off the booth can reach it.

### 5. First sync

The agent syncs within 30 seconds of starting. To watch one, press **Sync now** on the
booth's controller, or **Push to booth now** on production's IC dashboard (the booth
collects a push on its next check, within 30 seconds). Both show live progress and end
in one of:

| Result | Meaning |
|---|---|
| ✅ Succeeded | Every file is present and checksum-verified, and every film's player data matches production exactly. |
| ✅ Up to date | Nothing changed (scheduled and change-triggered syncs only; forced syncs always re-verify). |
| ⚠️ Partial | Some programs aren't playable yet; each one says why. If a master for a program that is showing now couldn't be fetched, the booth keeps its previous data until it can. |
| ❌ Failed | Nothing changed on the booth, which keeps playing its last verified data. The message says why. |

## Operating

- **What syncs.** Programs that are showing or upcoming (and yesterday's). A sync fetches
  the masters for programs already showing and all VTT files, then applies the data, then
  fetches masters for later programs, so a fix for tonight never waits behind next
  month's film. Downloads are throttled while a film plays.
- **When.** Within about 30 seconds of a change on production (the agent's check), when
  someone presses *Sync now* or *Push to booth now*, and every 15 minutes regardless.
- **Never mid-film.** A sync can apply at any time, even during a showing: the film that
  is playing keeps the annotations it loaded and streams from a URL that names one
  verified file, which no sync can change. New data takes effect when the next item loads.
- **Only copies.** After the booth verifies a master and its SD file exists, production
  deletes its copy (*Films and masters* says so). The booth keeps such files until 30 days
  after their last program, warns 7 days ahead, and re-hashes them weekly in the morning.
  Keep the original disc or file until the film's last showing; if the booth loses one,
  its programs show ❌ *re-upload required*, and uploading the same file again restores it.
- **Logs.**
  `journalctl --user -u <app>-ic-sync-agent -f` (booth),
  `journalctl --user -u <app>-media-worker -f` (production).
  The booth's *Sync status* page lists recent runs and the files on disk.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Push says *the booth hasn't checked in since …* | Booth off, offline, or its sync agent is stopped; check `systemctl --user status <app>-ic-sync-agent` on the booth. A push the booth doesn't collect within 30 minutes expires. |
| Booth badge: *Can't reach production* | Network, DNS or TLS between the booth and production, or a wrong `IC_PARENT_URL`/`IC_SYNC_TOKEN` (the *Sync status* page shows the error). Playback is unaffected. |
| *Sync agent isn't running* | `systemctl --user restart <app>-ic-sync-agent`. |
| `code_mismatch` | One side hasn't deployed the latest commit yet; it resolves within minutes. |
| `fingerprint_mismatch` | The booth computed different player data from production's rows; the booth kept its previous data. Report it: this is the corruption the design exists to prevent. |
| Output not fullscreen | Click the output window once; if it opens on the wrong screen, revisit the spike's flags. |
| Stutter at high bitrate | Check `chrome://media-internals` for a hardware decoder; lower `IC_BOOTH_VERIFIED_MAX` so oversized masters are flagged. |

## Moving off the Electron IC Player

1. Import existing films:
   `bash deploy/manage.sh import_ic_legacy /path/to/films --mapping mapping.csv --with-video`.
   The mapping CSV has `icf,content_id` rows (contents in IC playlists). `--with-video`
   ingests each film's `.ic/` video as its master, which is exactly what the legacy
   annotations were timed against; use `--dry-run` first.
2. Run the booth in parallel with the Electron player for a week or two; rehearse each
   program on the booth.
3. After a clean month, remove `IC_player/` (and its entry in
   `core/tests/test_js_constant_parity.py`).
