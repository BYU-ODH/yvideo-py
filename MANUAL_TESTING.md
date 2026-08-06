# Manual testing

Things this project cannot verify automatically, and how to check them by hand.

An item belongs here only if automating it is impossible or misleading — not merely
inconvenient. Most of these fail that way for the same reason: the behaviour lives in a
platform integration (iOS media handling, a screen reader, real display hardware) that our
headless browsers do not have, so a test would pass whether or not the code is correct.
**A test that cannot fail is worse than no test**, because it reads as coverage.

Where a cheap automated test can guard *part* of an item — usually that the relevant markup or
CSS is still present — it exists and is linked below. Those tests are noted as guarding
presence, not behaviour, so nobody mistakes one for the other.

## Status

| # | Item | Severity | Last verified |
|---|------|----------|---------------|
| 1 | [Inline playback and blur compositing on iOS](#1-inline-playback-and-blur-compositing-on-ios) | **Critical** | Never |
| 2 | [Remote playback is refused (AirPlay / Cast)](#2-remote-playback-is-refused-airplay--cast) | Medium | Never |
| 3 | [Blur editing by touch](#3-blur-editing-by-touch) | Medium | Never |
| 4 | [Blur announcements in a screen reader](#4-blur-announcements-in-a-screen-reader) | Medium | Never |
| 5 | [A blur actually obscures what is under it](#5-a-blur-actually-obscures-what-is-under-it) | **Critical** | Never |
| 6 | [Imported legacy blurs land on their subjects](#6-imported-legacy-blurs-land-on-their-subjects) | **Critical** | Never |

Severity reflects what a silent failure would expose. Blurs exist to cover copyrighted,
violent, and explicit content, so a blur that does not render — or renders but does not
conceal — is worse than no blur at all, because the person publishing the content believes it
is covered.

## Running the app on a real device

Items 1–3 need a phone or tablet pointed at a local server, which takes two changes:

1. Serve on the LAN: `uv run manage.py runserver 0.0.0.0:8000`
2. Add the machine's LAN address to `ALLOWED_HOSTS` in `yvideo/secret_settings.py`
   (it defaults to `localhost`/`127.0.0.1` only, so a phone otherwise gets a 400
   `DisallowedHost`)

`/login/dev/quick/` will **not** work from the phone — it is restricted to `localhost` and
`127.0.0.1` — so sign in through the normal form as `devadmin` / `devadmin`.

All of the steps below use the deterministic demo seed (`uv run manage.py seed_demo_data`) and
the *Birds Overview* content, which carries two seeded blurs: **Bird Watermark** (stationary,
one point) and **Bird Flight Path** (moving, three points).

---

## 1. Inline playback and blur compositing on iOS

Without `playsinline`, iOS hands playback to the OS fullscreen player, where our
`#annotation-box` overlay is not composited — **blur annotations silently do not apply.** No
error, no layout glitch, just no blur. This is the highest-severity untested item in the
project.

**Why it cannot be automated:** Playwright's `webkit` browser is WebKit built for the host OS.
Delegating playback to the system player is an iOS platform media-stack integration, not a
WebKit engine behaviour — desktop WebKit plays inline either way, so a browser test would pass
identically with `playsinline` deleted. Playwright's device emulation
(`playwright.devices["iPhone 13"]`) sets viewport, user agent, touch and device scale factor,
none of which affect media delegation. The iOS Simulator is not a substitute either; its media
stack differs from a physical device.

**Guarded automatically:** `InlinePlaybackAttributeTests` in
[core/tests/test_templates.py](core/tests/test_templates.py) asserts `playsinline`,
`webkit-playsinline` and `disableRemotePlayback` are still on the `<video>` in
[core/templates/core/partials/player-wrapper.html](core/templates/core/partials/player-wrapper.html).
That catches the realistic regression — an attribute dropped during an unrelated edit — but
proves nothing about iOS.

**On a physical iPhone**, at `/player/<content_id>/` for *Birds Overview*:

- [ ] Tap play. Playback stays **inside the page**; iOS does not take over the screen.
- [ ] The *Bird Watermark* blur is visible over the video while it plays.
- [ ] The *Bird Flight Path* blur **moves** with the bird rather than jumping between three
      fixed spots — this is the interpolation from `rectAtTime`, and it is what makes three
      points enough to keep a moving subject covered.
- [ ] Scrub the timeline. The blur keeps up while scrubbing, including while paused.
- [ ] Tap the fullscreen button. The blur is **still composited and still aligned** with the
      video. Check this specifically: `AnnotationPlayer.onFullscreenChange()`
      ([AnnotationPlayer.js:1327](core/static/js/AnnotationPlayer.js#L1327)) re-syncs the
      overlay there precisely because `ResizeObserver` timing across iOS native fullscreen is
      unreliable. If the overlay is ever offset, this transition is where it will show.
- [ ] Leave fullscreen. Still aligned.
- [ ] Rotate the device in both orientations, in and out of fullscreen. The blur tracks the
      letterbox/pillarbox padding rather than the element box.

---

## 2. Remote playback is refused (AirPlay / Cast)

`disableRemotePlayback` exists for the same reason as `playsinline`: a video routed to an Apple
TV or Chromecast is composited by the receiving device, which has never seen our overlay, so
blurs would not apply there either.

**Why it cannot be automated:** it needs a real receiver on the network. Headless browsers
expose no remote playback targets, so the code path is never entered.

**Guarded automatically:** attribute presence only, same test as item 1.

**With an Apple TV or Chromecast on the same network:**

- [ ] The AirPlay / Cast button does not appear in the video controls for this element, or is
      disabled.
- [ ] If the OS offers screen mirroring anyway (which `disableRemotePlayback` cannot prevent —
      it mirrors the whole display rather than routing the video), the blur is still visible,
      because the page itself is what is being mirrored.

---

## 3. Blur editing by touch

Placing a blur is a drag gesture. The editor uses Pointer Events with `setPointerCapture`, which
is the right API for this. Without `touch-action` a browser may still claim a touch drag as a page
scroll and fire `pointercancel` instead; `BlurEditor` handles that event and cancels the gesture
cleanly, so nothing is corrupted, but the drag silently does nothing. `preventDefault()` on
`pointerdown` is not a reliable substitute — the spec makes `touch-action` the mechanism, and
scroll arbitration happens before the handler runs.

`touch-action: none` is therefore set on all three drag surfaces: `#blur-edit-rig`,
`.blur-rig-handle`, and `.blur-position-locator`. What remains to verify on real hardware is
whether that is sufficient in practice, and whether the hit targets are usable with a fingertip.

**Why it cannot be automated:** Playwright can synthesise touch events, but the
scroll-vs-drag arbitration that would break this is done by the browser's compositor thread
against real touch input. Synthesised events bypass it, so the test would pass while a finger
fails — which is also why the `touch-action` rules above are not covered by the suite.

**On a phone or tablet**, at `/video-editor/<content_id>/`, with a blur selected:

- [ ] Drag the blur box with a finger. It moves; the page does not scroll instead.
- [ ] Drag each of the eight handles. They resize; the handles are large enough to hit with a
      fingertip. (They are deliberately small — 5px painted with a transparent border extending
      the hit target — because they sit on top of the content the blur exists to conceal, so
      this is a genuine trade-off to evaluate rather than a bug to fix reflexively.)
- [ ] Drag a blur point dot along the timeline bar to retime it.
- [ ] Drag the item's left and right edges on the timeline.
- [ ] Edit a number in the blur points table. The on-screen keyboard does not obscure the field
      being edited, and the value saves on commit.

---

## 4. Blur announcements in a screen reader

A blur is placed by dragging, and dragging is the one interaction a keyboard-only user cannot
perform. Their entire access to the feature is the rig's arrow keys, the focusable timeline
dots, and the numeric fields in the points table — so whether those are *announced* is not a
nicety.

**Why it cannot be automated:** axe-core checks that accessible names, roles and relationships
are present in the DOM. It cannot check what a screen reader actually says, which depends on the
pairing of reader and browser. In particular nothing automated verifies that the
`aria-live="polite"` status region
([blur_positions.html:125](core/templates/core/partials/blur_positions.html#L125)) is announced
when it changes, and a live region that never announces is indistinguishable from a correct one
in the markup.

**Guarded automatically:** `test_the_blur_editing_ui_has_no_a11y_violations` in
[tests/e2e/test_a11y.py](tests/e2e/test_a11y.py) runs axe over the points panel and the
editable video frame with a blur selected and a row active. That covers names, roles and
contrast — the static half.

**With VoiceOver (macOS/iOS) or NVDA (Windows)**, at `/video-editor/<content_id>/`:

- [ ] Selecting a blur announces the editing rig, its role and its label.
- [ ] Nudging the box with the arrow keys, then pressing Enter, announces the resulting status
      — *"Point added at 17.40s"*, *"Point updated at 17.40s"*, *"Point moved to 17.40s"* or
      *"Point deleted"*, depending on what the write did.
- [ ] The blur points table is navigable as a table: column headers (Time, X, Y, W, H) are
      announced with their cells, and each row's number is announced as its row header.
- [ ] The first point's Time field is announced as read-only, and its explanatory title is
      reachable.
- [ ] Each timeline dot is announced with its time, and Delete on a focused dot announces the
      removal.

---

## 5. A blur actually obscures what is under it

Everything automated about blurs verifies *geometry and plumbing*: that the box is at the right
percentage of the video frame, that its CSS is applied, that the numbers round-trip through the
database. **Nothing asserts that the pixels under the box are unreadable.** For the one feature
in this app whose whole purpose is concealment, that gap is worth a human eye.

**Why it cannot be automated:** the effect is `backdrop-filter: blur()`
([AnnotationPlayer.css:250](core/static/css/AnnotationPlayer.css#L250)), which is composited by
the GPU. Headless screenshots frequently do not render `backdrop-filter` at all — so a
pixel-diff assertion would either compare unblurred output (and pass a broken blur) or be
disabled as flaky. Judging "is this face still recognisable" is also not a threshold a test can
hold.

**In a real browser window** — repeat per browser the audience uses (Safari, Chrome, Firefox):

- [ ] Play a video with a blur over legible text. The text is **unreadable**, not merely soft.
- [ ] The blur strength holds up in fullscreen. The radius is `frame.height * 0.1` via
      `--blur-radius`, so it should scale with the frame rather than thinning out as the video
      gets larger.
- [ ] Nothing legible leaks at the blur's edges.
- [ ] With the subtitle sidebar open, the blur stays over the subject rather than shifting by
      the sidebar width.
- [ ] Confirm on a wide window and a narrow one that the blur still covers the subject — this
      exercises the pillarbox and letterbox padding, which is where a coordinate-space error
      would show up as a blur sliding off its target.

---

## 6. Imported legacy blurs land on their subjects

The legacy app anchored a censor's geometry at the box's **center**; this app stores the
**top-left** corner. Nothing converted between the two until Phase 6 of the blur work, so every
blur imported before that sits `width/2` percent right and `height/2` percent down from where
its author put it — the bottom-right of whatever it covered is exposed.

**Why it cannot be automated:** the arithmetic *is* automated, thoroughly — see
[core/tests/test_legacy_blur_import.py](core/tests/test_legacy_blur_import.py). What no test can
do is confirm that the resulting box covers **the thing the original author meant to cover**.
That requires the real legacy video, the real legacy censor, and someone who can look at the
frame and say whether the face is hidden. Our fixtures assert coordinates against expected
numbers; they cannot assert intent.

**After running a legacy migration**, for a sample of imported blurs:

- [ ] Open each in the video editor and play through its time range. The blur covers its subject
      for the whole range, not just at the first point.
- [ ] Compare against the same censor in the legacy app if it is still reachable. The box is in
      the same place, not offset down-and-right.
- [ ] Check a censor whose box sat near an edge of the frame. Converting can push the corner
      negative, and `BlurAnnotationPosition.save()` clamps it back onto the frame — so the box
      may be a little larger or shifted relative to the original. Confirm it still covers the
      subject rather than having slid off it.
- [ ] Check a censor that had several keyframes. It glides between them rather than jumping, and
      the timeline shows one dot per point after the first.

**No back-fill is needed.** The conversion landed before any content had been imported from the
legacy server, so there is no corpus of offset blurs in the database and nothing to correct. That
also means **the first real migration is the first time this code meets real legacy data** — the
checks above have only ever run against fixtures, which is exactly why they are worth doing by
hand rather than trusting the arithmetic tests alone.

---

## Adding to this file

When you find something that resists automation, add it here with:

1. **What to verify** — as a checklist someone else can follow without your context.
2. **Why it cannot be automated** — the specific mechanism, not "it's hard". This is the part
   that stops a future contributor from replacing the item with a test that cannot fail.
3. **What is guarded automatically**, if anything, and what that guard does *not* prove.
4. A row in the status table, with the severity of a silent failure.

If you verify an item, put the date and the device/browser in the *Last verified* column.
