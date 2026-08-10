"""The fake YouTube API and the real one are held to one shared expectation table.

Nearly every YouTube test here runs against tests/e2e/fake_youtube.py, which buys offline, fast and
takedown-proof runs at one cost: the fake can drift. If YouTube changes a behaviour our adapter
relies on - or, more likely, if the fake was subtly wrong about one from the start - a green suite
would keep saying so while production broke.

This file is the alarm for that. `PROBE_JS` drives a `youtube-video` element through the whole
lifecycle our code depends on and returns a report of *normalized* observations: booleans, small
integers, and the order in which events first appeared. Nothing in it is a raw timing or a
video-specific value, so the same report is expected from both backends, and both are asserted
against the same `EXPECTED_CONTRACT` literal below. A divergence fails as a named key, which says
what drifted - more useful than a diff of two nested dicts.

Deliberately outside the comparison, because the fake is not trying to reproduce them:

- **How long a seek takes.** The fake takes 250ms on purpose (fake_youtube.SEEK_LATENCY_MS): a
  synchronous fake seek makes the stale-overlay bug `_beginSeek` exists to fix unreproducible. What
  is compared is that a seek is asynchronous, settles, and reports `seeking` -> `timeupdate` ->
  `seeked` on the way - the shape our code is written against.
- **The embed URL and the iframe's geometry.** The fake builds its own about:blank iframe, so its
  src carries no player vars and its size is not the API's choice being overridden by our CSS.
  test_youtube_video_controls.py asserts both against the real API.
- **Buffering churn.** The real player drops in and out of BUFFERING, which our element forwards as
  extra play/pause events. Each phase below therefore compares only the events that phase's code is
  responsible for, filtered by an allowlist.
- **`ended`.** Reaching it live means playing a video to its end, which is minutes of wall clock and
  an ad break away from being reliable. Covered against the fake only; manual item 7 in
  MANUAL_TESTING.md is the backstop.

The live half skips - it does not fail - when youtube.com is unreachable or the pinned video has
been unpublished. See the `live_youtube` fixture in conftest.py.
"""

import json
from pathlib import Path
import re

import pytest

from tests.e2e.live_youtube import LIVE_EMBED_REFUSED_MESSAGE

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# The methods our element actually calls on the player object are extracted from the source rather
# than listed here, so adding a call to YouTubeVideoElement.js automatically starts asserting that
# both the fake and the real API provide it.
_YOUTUBE_ELEMENT_SOURCE = (
    Path(__file__).resolve().parents[2] / "core/static/js/YouTubeVideoElement.js"
)
_PLAYER_CALL_PATTERN = re.compile(r"this\._player\??\.(\w+)\(")


def player_methods_we_call():
    names = sorted(
        set(_PLAYER_CALL_PATTERN.findall(_YOUTUBE_ELEMENT_SOURCE.read_text()))
    )
    # A rename of `this._player` would otherwise leave this list empty and every assertion below
    # about it trivially true.
    assert {"getCurrentTime", "getDuration", "seekTo", "playVideo"} <= set(names), (
        f"extracted an implausible set of player calls from {_YOUTUBE_ELEMENT_SOURCE.name}: "
        f"{names} - has the field been renamed?"
    )
    return names


# Drives a freshly created element rather than the page's own, so the listeners are attached before
# the player is constructed and `loadedmetadata` can be observed at all. It is the same custom
# element class the page uses, on the page where production uses it.
PROBE_JS = """
async ({videoId, methods}) => {
  const EVENTS = ["loadedmetadata", "play", "playing", "pause", "timeupdate",
                  "seeking", "seeked", "ended", "error"];
  const report = {};
  const log = [];

  const element = document.createElement("youtube-video");
  element.dataset.videoId = videoId;
  // On screen and sized: a real embed will not play in a zero-sized or hidden element.
  Object.assign(element.style, {
    position: "fixed", left: "0px", bottom: "0px", width: "640px", height: "360px",
  });
  EVENTS.forEach((name) => element.addEventListener(name, () => log.push(name)));
  document.body.appendChild(element);

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const waitFor = async (predicate, timeout) => {
    const startedAt = performance.now();
    while (performance.now() - startedAt < timeout) {
      if (predicate()) return true;
      await sleep(50);
    }
    return false;
  };
  // First-appearance order, not every occurrence: the real player repeats play/timeupdate as it
  // rebuffers, and how many times it did that is not part of any contract. What events happened,
  // and which came first, is.
  const eventOrder = (...allowed) => {
    const seen = log.filter((name) => allowed.includes(name));
    return seen.filter((name, index) => seen.indexOf(name) === index);
  };
  const reset = () => { log.length = 0; };

  report.becameReady = await waitFor(() => element.readyState > 0, 20000);
  if (!report.becameReady) {
    // Everything below would read as a cascade of unrelated failures. Report the one that matters
    // and let the caller see a short dict.
    report.eventsWhileWaiting = eventOrder(...EVENTS);
    element.remove();
    return report;
  }

  // The element clears YouTube's splash by playing and immediately pausing
  // (YouTubeVideoElement._primeFirstFrame). Wait that out before measuring anything: everything
  // below is about the API's own behaviour, and priming both answers `muted` locally while it runs
  // and ends with a seek that would race the play phase.
  // Both halves matter. `_priming` going false means the round trip is over; `muted` reading false
  // means priming's closing commands have actually landed at the player, which is a command latency
  // later. Without the second half the mute phase below intermittently sees the player still muted
  // from priming and concludes that mute was synchronous. The closing seek is queued before the
  // unmute, so waiting for the unmute waits for both.
  report.primingSettles = await waitFor(
    () => element._priming === false && element.muted === false, 15000);

  report.metadataEvents = eventOrder("loadedmetadata", "error");
  report.durationIsPositiveFinite =
    Number.isFinite(element.duration) && element.duration > 0;
  report.readyState = element.readyState;
  report.pausedBeforePlay = element.paused;
  report.seekingBeforePlay = element.seeking;
  report.endedBeforePlay = element.ended;
  report.errorIsNull = element.error === null;
  report.videoWidth = element.videoWidth;
  report.videoHeight = element.videoHeight;
  report.textTracksLength = element.textTracks.length;

  // The API is handed a <div> and is expected to replace it with an iframe. AnnotationPlayer's CSS
  // sizes `youtube-video > *`, so an extra wrapper or a surviving mount would change the geometry
  // the annotation overlay is computed from.
  report.mountWasReplaced = element.querySelector(":scope > div") === null;
  report.childCount = element.children.length;
  report.childTag = element.children[0]?.tagName.toLowerCase() ?? null;

  report.playerMethods = {};
  methods.forEach((name) => {
    report.playerMethods[name] = typeof element._player?.[name] === "function";
  });
  const states = window.YT?.PlayerState ?? {};
  report.playerStateConstants = {
    UNSTARTED: states.UNSTARTED ?? null,
    ENDED: states.ENDED ?? null,
    PLAYING: states.PLAYING ?? null,
    PAUSED: states.PAUSED ?? null,
    BUFFERING: states.BUFFERING ?? null,
    CUED: states.CUED ?? null,
  };

  // Muted, so a real embed is allowed to start without a user gesture. `muted` reads back through
  // the player rather than from our own state, so this doubles as a check on isMuted() - and on
  // when isMuted() starts telling the truth.
  reset();
  element.muted = true;
  report.muteIsAsynchronous = element.muted === false;
  report.muteSettles = await waitFor(() => element.muted === true, 5000);

  const timeBeforePlay = element.currentTime;
  element.play();
  report.playIsAsynchronous = element.paused === true;
  report.playStarts = await waitFor(() => !element.paused, 20000);
  if (!report.playStarts) {
    // Nothing below can be measured on a player that will not play, and every phase would spend its
    // own timeout finding that out - a minute of waiting to report the same thing. The caller
    // decides whether this is a refusal to skip over or a regression to fail on.
    element.remove();
    return report;
  }
  report.playAdvancesTime =
    await waitFor(() => element.currentTime > timeBeforePlay + 0.3, 20000);
  // `currentTime` reads the player directly, but `timeupdate` and `buffered` are both fed by the
  // element's own POLL_INTERVAL_MS poll - so a warm player can cross the threshold above before the
  // first poll fires. Waiting for it here is what keeps the next three observations from racing it.
  await waitFor(() => log.includes("timeupdate"), 5000);
  report.playEvents = eventOrder("play", "playing", "timeupdate");
  report.bufferedLength = element.buffered.length;
  report.loadedFractionIsPositive = element.buffered.end(0) > 0;

  reset();
  element.pause();
  report.pauseIsAsynchronous = element.paused === false;
  report.pauseStops = await waitFor(() => element.paused, 10000);
  const timeAtPause = element.currentTime;
  await sleep(600);
  report.pauseFreezesTime = Math.abs(element.currentTime - timeAtPause) < 0.15;
  report.pauseEvents = eventOrder("pause");

  // A seek while paused, which is what every scrub in the editor is. The one case the IFrame API
  // reports nothing at all for.
  reset();
  const seekTarget = Math.min(10, Math.max(2, element.duration / 2));
  element.currentTime = seekTarget;
  // `seeking` is ours to dispatch, so it is immediate; the player's own position is not, so
  // currentTime still reads the old one. Everything between is what `_beginSeek` covers.
  report.seekingIsSynchronous = log.includes("seeking");
  report.seekedIsAsynchronous = !log.includes("seeked");
  report.seekIsAsynchronous = Math.abs(element.currentTime - seekTarget) > 0.5;
  report.seekSettles =
    await waitFor(() => log.includes("seeked") && !element.seeking, 10000);
  report.seekEvents = eventOrder("seeking", "timeupdate", "seeked");
  // 1s, not the element's own 0.5s tolerance: a real player may land on a nearby keyframe, and
  // `_beginSeek` gives up after 1s anyway, so anything looser would mean the element declares
  // seeks finished that never arrived.
  report.seekArrives = Math.abs(element.currentTime - seekTarget) < 1;
  report.stillPausedAfterSeek = element.paused;

  element.remove();
  return report;
}
"""

# Every key the probe returns on a healthy player, and the value both backends have to report.
# Compared as a whole dict rather than key by key, so a measurement added to PROBE_JS and left out
# of this table fails as an unexpected key instead of being quietly collected and never asserted.
EXPECTED_CONTRACT = {
    "becameReady": True,
    # Both players let the splash-clearing play/pause round trip complete. On the real API this is
    # also the only check that autoplay-while-muted is still permitted at all: if a browser policy
    # or an embed setting refused it, priming would time out here instead of settling.
    "primingSettles": True,
    "metadataEvents": ["loadedmetadata"],
    "durationIsPositiveFinite": True,
    # HAVE_METADATA. The element promises no more than this, and the API gives it no way to.
    "readyState": 1,
    "pausedBeforePlay": True,
    "seekingBeforePlay": False,
    "endedBeforePlay": False,
    "errorIsNull": True,
    # The constant 16:9 the overlay geometry is derived from. Not a measurement of the video - see
    # the comment on YouTubeVideoElement's videoWidth.
    "videoWidth": 1280,
    "videoHeight": 720,
    "textTracksLength": 0,
    "mountWasReplaced": True,
    "childCount": 1,
    "childTag": "iframe",
    "playerStateConstants": {
        "UNSTARTED": -1,
        "ENDED": 0,
        "PLAYING": 1,
        "PAUSED": 2,
        "BUFFERING": 3,
        "CUED": 5,
    },
    # Not one command on this API takes effect synchronously - every one is a postMessage into the
    # iframe whose reply lands a task or more later - and every one of them does settle. That pair
    # is the single most load-bearing fact about this boundary: code that sets a property and reads
    # it back in the same tick gets the *old* value. The `...IsAsynchronous` keys are what stop the
    # fake from quietly becoming a player that answers instantly, which is what it used to be.
    "muteIsAsynchronous": True,
    "muteSettles": True,
    "playIsAsynchronous": True,
    "playStarts": True,
    "playAdvancesTime": True,
    "playEvents": ["play", "playing", "timeupdate"],
    "bufferedLength": 1,
    "loadedFractionIsPositive": True,
    "pauseIsAsynchronous": True,
    "pauseStops": True,
    "pauseFreezesTime": True,
    "pauseEvents": ["pause"],
    "seekingIsSynchronous": True,
    "seekedIsAsynchronous": True,
    "seekIsAsynchronous": True,
    "seekSettles": True,
    "seekEvents": ["seeking", "timeupdate", "seeked"],
    "seekArrives": True,
    "stillPausedAfterSeek": True,
}


def _probe(page, video_id):
    """Run the probe on a page that already has the element defined, and return its report."""
    methods = player_methods_we_call()
    report = page.evaluate(PROBE_JS, {"videoId": video_id, "methods": methods})
    if not report.get("becameReady"):
        # The short report the probe bailed out with says more than a method check on a player that
        # never existed; the caller's comparison against the table names it.
        return report
    assert report.pop("playerMethods") == dict.fromkeys(methods, True), (
        "YouTubeVideoElement calls a player method this player does not have: "
        f"{json.dumps(report.get('playerMethods'))}"
    )
    return report


# Observations that say "this machine can play a YouTube video" rather than "the fake and the real
# API agree". If any is false there is nothing to compare: the rest of the report describes a player
# that never ran, and asserting on it would report a dozen divergences that mean nothing.
_PLAYABILITY_KEYS = ("errorIsNull", "becameReady", "playStarts", "playAdvancesTime")


def _skip_unless_the_embed_actually_played(report):
    refused = [key for key in _PLAYABILITY_KEYS if not report.get(key)]
    if refused:
        pytest.skip(
            f"{LIVE_EMBED_REFUSED_MESSAGE} (observed: {', '.join(refused)} false)"
        )


def _open_editor_on(page, live_server, content, require_live_embed=None):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    # Live only: skips, rather than letting the wait below time out, when this machine cannot get an
    # embed to load at all.
    if require_live_embed:
        require_live_embed()
    # The custom element has to be defined and window.YT loaded before the probe creates its own
    # element; the page's own player reaching a duration is the cheapest proof of both.
    page.wait_for_function(
        """() => {
            const yt = document.querySelector('youtube-video');
            return Boolean(customElements.get('youtube-video') && window.YT?.Player
                && yt && !isNaN(yt.duration) && yt.duration > 0);
        }""",
        timeout=20000,
    )


def test_the_fake_satisfies_the_contract(
    fake_youtube, live_server, youtube_content, page
):
    content = youtube_content("Contract - Fake API")
    _open_editor_on(page, live_server, content)

    assert _probe(page, "contract0000") == EXPECTED_CONTRACT


@pytest.mark.live_youtube
def test_the_real_api_satisfies_the_same_contract(
    live_youtube, require_live_embed, live_server, youtube_content, page
):
    """The other half of the pair: the same table, against youtube.com.

    Both halves comparing to one literal is what makes the fake and the real API comparable at all.
    If this fails and test_the_fake_satisfies_the_contract passes, the fake has drifted (or YouTube
    has changed) on whichever key is named - and the fake, not this table, is what to fix, unless
    the real behaviour is one our code cannot live with.

    Note this does *not* request the `fake_youtube` fixture, which would install the stub and assert
    that nothing reached youtube.com. Here reaching it is the point.
    """
    content = youtube_content("Contract - Live API", video_id=live_youtube)
    _open_editor_on(page, live_server, content, require_live_embed)

    report = _probe(page, live_youtube)
    _skip_unless_the_embed_actually_played(report)
    assert report == EXPECTED_CONTRACT
