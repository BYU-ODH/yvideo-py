"""A stand-in for YouTube's IFrame Player API, installed before the page's own scripts run.

Every YouTube test used to need youtube.com reachable *and* one particular third-party video
still published, which makes an offline machine and someone else's takedown both look like a
regression in this repo. What most of those tests actually exercise is our side of the boundary -
YouTubeVideoElement's translation of the API into HTMLVideoElement semantics, and the geometry
AnnotationPlayer derives from the element - so the API can be faked without weakening them.

What a fake cannot cover is the boundary itself: that the real API injects an iframe which honours
`width: 100%`, and that the player draws the picture where contentRect() expects. That is why
tests/e2e/test_youtube_video_controls.py still runs against the live API, and why manual item 7 in
MANUAL_TESTING.md exists.

What a fake can always do is drift. tests/e2e/test_youtube_api_contract.py runs one probe against
this and against youtube.com and holds both to the same expectation table, so a divergence fails as
a named observation instead of quietly making these tests describe a player that does not exist.
The latencies below are what that comparison measured, and they are load-bearing: see the comment
on COMMAND_LATENCY_MS.

`YouTubeVideoElement.loadIframeApi` returns early when `window.YT.Player` is already defined, so
installing this as an init script also means no request to youtube.com is ever made.
"""

FAKE_IFRAME_API_JS = """
(() => {
  const PlayerState = {
    UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5,
  };
  const DEFAULT_DURATION_SECONDS = 60;
  const TICK_MS = 100;

  // Nothing the real API exposes takes effect synchronously. Every command is a postMessage into
  // the iframe, and the player's own getters keep reporting the old value until the answer comes
  // back - measured against youtube.com at ~6ms for pause/mute/setVolume, ~34ms for a seek to
  // land, and ~270ms before playback actually starts, because the player buffers first.
  //
  // This is not incidental fidelity. A synchronous fake makes every read-back-immediately bug in
  // our own code unreproducible: the stale-overlay bug YouTubeVideoElement._beginSeek exists to
  // fix, and AnnotationPlayer's `toggleMute(); ...this.videoElem.muted` pair, are both invisible
  // against a player that answers instantly. Tests written against one would pass either way.
  const COMMAND_LATENCY_MS = 10;
  const PLAY_LATENCY_MS = 250;
  // Deliberately slower than the ~34ms measured above: a seek is the one command whose latency our
  // code has to actively cope with, so the fake takes the pessimistic end of it.
  const SEEK_LATENCY_MS = 250;
  // A video id that fails instead of loading, standing in for the removed / private /
  // embedding-disabled cases. There is no other way to reach that path on demand: a real
  // unplayable video would have to be found, and would then be one takedown away from becoming
  // a playable-video test that silently stops covering anything.
  const ERROR_VIDEO_ID = "error000000";

  class FakePlayer {
    constructor(mount, options) {
      this._events = options.events || {};
      // Read here rather than at install time, so a test can set the global in its own init
      // script without having to care which script runs first. Length matters to anything that
      // works in percentages of the duration - see test_youtube_editor_item_resize.py.
      this._duration = window.__fakeYouTubeDurationSeconds || DEFAULT_DURATION_SECONDS;
      this._time = 0;
      this._playing = false;
      this._ticker = null;
      this._seekTimer = null;
      this._transitionTimer = null;
      this._commandTimers = new Set();
      this._muted = false;
      this._volume = 100;
      this._rate = 1;

      // The real API replaces the element it is handed with an iframe; matching that matters,
      // because AnnotationPlayer.css sizes `youtube-video > *` rather than a known tag.
      const iframe = document.createElement("iframe");
      // about:blank, so nothing here reaches the network. The params the real API would put in
      // the embed URL are recorded as attributes for any test that wants to assert on them.
      iframe.src = "about:blank";
      iframe.dataset.videoId = options.videoId || "";
      iframe.dataset.playerVars = JSON.stringify(options.playerVars || {});
      iframe.setAttribute("width", options.width == null ? "100%" : options.width);
      iframe.setAttribute("height", options.height == null ? "100%" : options.height);
      mount.replaceWith(iframe);
      this._iframe = iframe;

      // Asynchronous, like the real one: code that assumes onReady can fire during construction
      // would pass here and fail in production.
      setTimeout(() => {
        if (options.videoId === ERROR_VIDEO_ID) {
          // 150 is the real API's "the owner does not allow this video to be embedded".
          if (this._events.onError) this._events.onError({data: 150, target: this});
          return;
        }
        if (this._events.onReady) this._events.onReady({target: this});
      }, 0);
    }

    _emit(state) {
      if (this._events.onStateChange) {
        this._events.onStateChange({data: state, target: this});
      }
    }

    _stopTicker() {
      if (this._ticker !== null) {
        clearInterval(this._ticker);
        this._ticker = null;
      }
    }

    _startTicker() {
      this._stopTicker();
      this._ticker = setInterval(() => {
        this._time = Math.min(this._duration, this._time + TICK_MS / 1000);
        if (this._time >= this._duration) {
          this._playing = false;
          this._stopTicker();
          this._emit(PlayerState.ENDED);
        }
      }, TICK_MS);
    }

    // Every setter goes through here, so none of them can be read back synchronously. Tracked so
    // destroy() cannot leave a timer running against a torn-down player.
    _later(fn, delay) {
      const timer = setTimeout(() => {
        this._commandTimers.delete(timer);
        fn();
      }, delay === undefined ? COMMAND_LATENCY_MS : delay);
      this._commandTimers.add(timer);
      return timer;
    }

    _cancelTransition() {
      if (this._transitionTimer !== null) {
        clearTimeout(this._transitionTimer);
        this._transitionTimer = null;
      }
    }

    playVideo() {
      // A pause on its way out is superseded, the way it would be at the real player: the last
      // command to arrive is the one that decides.
      this._cancelTransition();
      if (this._playing) return;
      // The real player says it is buffering first, and only then that it is playing.
      // YouTubeVideoElement ignores BUFFERING; emitting it keeps that path exercised.
      this._emit(PlayerState.BUFFERING);
      this._transitionTimer = setTimeout(() => {
        this._transitionTimer = null;
        this._playing = true;
        this._startTicker();
        this._emit(PlayerState.PLAYING);
      }, PLAY_LATENCY_MS);
    }

    pauseVideo() {
      this._cancelTransition();
      if (!this._playing) return;
      // The ticker keeps running until the pause actually lands, as the real player's time does.
      this._transitionTimer = setTimeout(() => {
        this._transitionTimer = null;
        this._playing = false;
        this._stopTicker();
        this._emit(PlayerState.PAUSED);
      }, COMMAND_LATENCY_MS);
    }

    // Like the real player: asynchronous, and it does not change whether playback is running -
    // which together are the whole reason YouTubeVideoElement has to synthesize seeking/seeked.
    seekTo(seconds, _allowSeekAhead) {
      const target = Math.max(0, Math.min(this._duration, seconds));
      if (this._seekTimer !== null) clearTimeout(this._seekTimer);
      this._seekTimer = setTimeout(() => {
        this._seekTimer = null;
        this._time = target;
      }, SEEK_LATENCY_MS);
    }

    getCurrentTime() { return this._time; }
    getDuration() { return this._duration; }
    getVideoLoadedFraction() { return 1; }
    getPlayerState() { return this._playing ? PlayerState.PLAYING : PlayerState.PAUSED; }
    mute() { this._later(() => { this._muted = true; }); }
    unMute() { this._later(() => { this._muted = false; }); }
    isMuted() { return this._muted; }
    setVolume(volume) { this._later(() => { this._volume = volume; }); }
    getVolume() { return this._volume; }
    setPlaybackRate(rate) { this._later(() => { this._rate = rate; }); }
    getPlaybackRate() { return this._rate; }
    destroy() {
      this._stopTicker();
      this._cancelTransition();
      if (this._seekTimer !== null) clearTimeout(this._seekTimer);
      this._commandTimers.forEach((timer) => clearTimeout(timer));
      this._commandTimers.clear();
      this._iframe.remove();
    }
  }

  window.YT = {Player: FakePlayer, PlayerState: PlayerState};
  if (window.onYouTubeIframeAPIReady) {
    window.onYouTubeIframeAPIReady();
  }
})();
"""

# The default duration above, for tests that need to seek to a specific time. Override it per test
# with `page.add_init_script("window.__fakeYouTubeDurationSeconds = <seconds>;")`.
FAKE_DURATION_SECONDS = 60

# Pass this as a Content's video id to exercise the unplayable-video path.
ERROR_VIDEO_ID = "error000000"


def install_fake_youtube(page):
    """Make `window.YT` exist on every document this page loads, before its own scripts run."""
    page.add_init_script(FAKE_IFRAME_API_JS)
