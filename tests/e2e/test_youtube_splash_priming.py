"""A YouTube embed is nudged past its own splash screen, without the app noticing.

Until an embed has played, the iframe shows YouTube's poster frame and big play button. That sits
inside the iframe - on top of the picture, under the annotation overlay pinned to it - so the editor
opens on YouTube's chrome rather than on the frame being annotated. A <video> with no `poster`
attribute shows its first frame instead, which is the behaviour the rest of the app is written
against.

YouTubeVideoElement._primeFirstFrame closes that gap by playing and immediately pausing. The point
of these tests is that it stays invisible: the app is told nothing, the playhead comes back to where
it was, and the user's mute setting survives. A priming pass that leaked a `play` event would start
animation loops and flip the play button; one that left the playhead 0.3s in would silently move
every "start from the beginning" the editor does.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# Records what the element asks of the player, in order, by wrapping the fake's methods. Installed
# after fake_youtube's init script, so window.YT already exists. The recipe below is the whole
# assertion: proving priming happened means proving the player was driven, and the element's own
# surface is deliberately silent about it.
SPY_JS = """
(() => {
  window.__playerCalls = [];
  const spied = ["playVideo", "pauseVideo", "seekTo", "mute", "unMute"];
  spied.forEach((name) => {
    const original = window.YT.Player.prototype[name];
    window.YT.Player.prototype[name] = function (...args) {
      window.__playerCalls.push(name);
      return original.apply(this, args);
    };
  });
})();
"""

# Every event the element could leak while priming, recorded from before the player is constructed.
LISTEN_JS = """
(() => {
  window.__leakedEvents = [];
  const watch = (element) => {
    ["play", "playing", "pause", "timeupdate", "seeking", "seeked", "ended"].forEach((name) =>
      element.addEventListener(name, () => window.__leakedEvents.push(name)));
  };
  // The element is in the initial HTML, so it may already exist by the time this runs; if not, it
  // is upgraded during parsing and this catches it then.
  document.addEventListener("DOMContentLoaded", () => {
    const element = document.querySelector("youtube-video");
    if (element) watch(element);
  });
  const existing = document.querySelector("youtube-video");
  if (existing) watch(existing);
})();
"""


@pytest.fixture
def primed_editor(fake_youtube, live_server, youtube_content, page):
    """The editor on YouTube content, with the player's calls and the element's events recorded."""
    page.add_init_script(SPY_JS)
    page.add_init_script(LISTEN_JS)
    content = youtube_content("Splash Priming")

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    # Priming is over when the element says so. Waiting on that rather than a sleep is what makes
    # the assertions below about a settled player rather than a half-primed one.
    page.wait_for_function(
        """() => {
            const element = document.querySelector('youtube-video');
            return Boolean(element && element.readyState > 0 && element._priming === false);
        }""",
        timeout=15000,
    )
    return page


def test_the_player_is_played_and_paused_to_clear_the_splash(primed_editor, page):
    calls = page.evaluate("() => window.__playerCalls")

    # In this order: muted (a browser will not autoplay otherwise), played, paused, put back to the
    # start, and unmuted. `seekTo` before `unMute` matters less than that both happened.
    assert calls[:4] == ["mute", "playVideo", "pauseVideo", "seekTo"], (
        f"the player was not driven through the priming recipe: {calls}"
    )
    assert "unMute" in calls, (
        f"the priming mute was never lifted, so the video is left silent: {calls}"
    )


def test_priming_leaves_the_element_where_it_started(primed_editor, page):
    # Priming's closing seek and unmute are commands *to* the player, and nothing on this API takes
    # effect in the tick it was asked in (~10ms at the fake, ~6ms measured at youtube.com - see
    # test_youtube_api_contract.py). Reading in the same breath as `_priming` going false would be
    # measuring that gap rather than the state priming leaves behind.
    page.wait_for_timeout(100)
    state = page.evaluate(
        """() => {
            const element = document.querySelector('youtube-video');
            return {
                paused: element.paused,
                currentTime: element.currentTime,
                muted: element.muted,
                ended: element.ended,
                seeking: element.seeking,
            };
        }"""
    )

    assert state["paused"] is True, "priming left the video playing"
    assert state["currentTime"] == pytest.approx(0, abs=0.2), (
        f"priming left the playhead at {state['currentTime']:.2f}s instead of the beginning"
    )
    assert state["muted"] is False, "priming left the video muted"
    assert state["ended"] is False and state["seeking"] is False


def test_priming_is_invisible_to_the_app(primed_editor, page):
    leaked = page.evaluate("() => window.__leakedEvents")

    # A `play` here would have started AnnotationPlayer's animation loops and flipped its play
    # button for a playback nobody asked for; a `timeupdate` would have moved the scrubber.
    assert leaked == [], (
        f"priming dispatched media events the app never asked for: {leaked}"
    )
