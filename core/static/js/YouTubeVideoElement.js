// A custom element that wraps the YouTube IFrame Player API and exposes the
// small subset of the HTMLVideoElement surface that AnnotationPlayer.js
// actually touches (currentTime/duration/paused/muted/volume/playbackRate,
// readyState/seeking/ended, play()/pause(), textTracks/buffered stubs, and the
// events AnnotationPlayer listens for). This lets AnnotationPlayer treat a
// YouTube embed exactly like a <video> element without any changes to
// AnnotationPlayer.js itself.
//
// The IFrame API's event surface is narrower than a media element's, so some of
// those events are synthesized here rather than forwarded - see `_beginSeek`,
// which is the difference between annotations that follow a scrub and
// annotations that silently describe the wrong frame.

const IFRAME_API_SRC = "https://www.youtube.com/iframe_api";
const POLL_INTERVAL_MS = 250;
const DEFAULT_WIDTH = 1280;
const DEFAULT_HEIGHT = 720;

// How the synthetic seeking/seeked pair below is timed. The IFrame API has no seek-complete
// callback, so a seek is considered finished once the player reports a time near the one asked
// for. The timeout is the backstop: seeking past the end, or to a spot the player snaps away
// from, would otherwise leave `seeked` pending forever - and every consumer of it waiting.
const SEEK_POLL_INTERVAL_MS = 50;
const SEEK_TOLERANCE_SECONDS = 0.5;
const SEEK_TIMEOUT_MS = 1000;

const PRIME_TIMEOUT_MS = 5000;

// Mirrors HTMLMediaElement's readyState constants, the only two values this element can honestly
// distinguish: the player object exists (so duration and seeking work) or it does not.
const HAVE_NOTHING = 0;
const HAVE_METADATA = 1;

// MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED: the closest match for "YouTube will not play this",
// which covers a removed, private or embedding-disabled video alike.
const MEDIA_ERR_SRC_NOT_SUPPORTED = 4;

let iframeApiPromise = null;

function loadIframeApi() {
  if (!iframeApiPromise) {
    iframeApiPromise = new Promise((resolve) => {
      if (window.YT && window.YT.Player) {
        resolve(window.YT);
        return;
      }
      const previousCallback = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        previousCallback?.();
        resolve(window.YT);
      };
      const script = document.createElement("script");
      script.src = IFRAME_API_SRC;
      document.head.appendChild(script);
    });
  }
  return iframeApiPromise;
}

export class YouTubeVideoElement extends HTMLElement {
  constructor() {
    super();
    this._state = {
      currentTime: 0,
      duration: NaN,
      paused: true,
      muted: false,
      volume: 1,
      playbackRate: 1,
      loadedFraction: 0,
      seeking: false,
      ended: false,
      error: null,
    };
    this._player = null;
    this._ready = false;
    this._pending = [];
    this._pollTimer = null;
    this._seekTimer = null;
    this._initialized = false;
    this._priming = false;
    this._primeTimer = null;
    this._playRequested = false;
  }

  connectedCallback() {
    // AnnotationPlayer's constructor wraps the video element in a
    // `.video-wrapper` div via appendChild, which reparents this (already
    // connected) element and fires disconnectedCallback/connectedCallback
    // again per the custom-elements spec. Guard so we don't tear down and
    // recreate the player on that reparent.
    if (this._initialized) return;
    this._initialized = true;

    const videoId = this.dataset.videoId;
    const mount = document.createElement("div");
    this.appendChild(mount);

    loadIframeApi().then((YT) => {
      this._player = new YT.Player(mount, {
        videoId: videoId,
        width: "100%",
        height: "100%",
        // controls:0 hides YouTube's own control bar entirely - AnnotationPlayer
        // provides all playback UI. disablekb:1 stops YouTube from also handling
        // keyboard shortcuts (space/arrows) that AnnotationPlayer.js already
        // listens for. iv_load_policy:3 suppresses YouTube's video annotation
        // cards, which would otherwise overlay the video the same way.
        playerVars: {
          playsinline: 1,
          modestbranding: 1,
          rel: 0,
          controls: 0,
          disablekb: 1,
          iv_load_policy: 3,
        },
        events: {
          onReady: () => this._onReady(),
          onStateChange: (event) => this._onStateChange(event),
          onError: () => this._onError(),
        },
      });
    });
  }

  disconnectedCallback() {
    // Reparenting (see connectedCallback above) also triggers a
    // disconnect/reconnect pair synchronously within the same call, so by
    // the time this microtask runs, `isConnected` is back to `true` if this
    // was just a move. Only tear down on a genuine removal from the document.
    queueMicrotask(() => {
      if (this.isConnected) return;
      this._stopPolling();
      this._clearSeekTimer();
      this._clearPrimeTimer();
      this._priming = false;
      this._player?.destroy();
      this._player = null;
      this._ready = false;
      this._pending = [];
      this._initialized = false;
    });
  }

  _onReady() {
    this._ready = true;
    this._state.duration = this._player.getDuration();
    this._player.setVolume(this._state.volume * 100);
    if (this._state.muted) {
      this._player.mute();
    }

    const pending = this._pending;
    this._pending = [];
    pending.forEach((fn) => fn());

    this.dispatchEvent(new Event("loadedmetadata"));
    this._primeFirstFrame();
  }

  _primeFirstFrame() {
    if (this._playRequested) return;
    this._priming = true;
    this._player.mute();  // Youtube does not allow autoplay with sound.
    this._player.playVideo();
    this._primeTimer = setTimeout(() => this._cancelPriming(), PRIME_TIMEOUT_MS);
  }

  _clearPrimeTimer() {
    if (this._primeTimer) {
      clearTimeout(this._primeTimer);
      this._primeTimer = null;
    }
  }

  _restoreMuteAfterPriming() {
    if (this._state.muted) {
      this._player.mute();
    } else {
      this._player.unMute();
    }
  }

  _finishPriming() {
    this._clearPrimeTimer();
    this._priming = false;
    this._player.seekTo(this._state.currentTime, true);
    this._restoreMuteAfterPriming();
  }

  _cancelPriming() {
    if (!this._priming) return;
    this._clearPrimeTimer();
    this._priming = false;
    this._restoreMuteAfterPriming();
    if (this._player.getPlayerState() === window.YT.PlayerState.PLAYING) {
      this._onStateChange({ data: window.YT.PlayerState.PLAYING });
    }
  }

  _onStateChange(event) {
    const YT = window.YT;
    if (this._priming) {
      if (event.data === YT.PlayerState.PLAYING) {
        this._player.pauseVideo();
      } else if (event.data === YT.PlayerState.PAUSED) {
        this._finishPriming();
      }
      return;
    }
    if (event.data === YT.PlayerState.PLAYING) {
      this._state.paused = false;
      this._state.ended = false;
      this._startPolling();
      this.dispatchEvent(new Event("play"));
      this.dispatchEvent(new Event("playing"));
    } else if (event.data === YT.PlayerState.PAUSED) {
      this._state.paused = true;
      this._state.ended = false;
      this._stopPolling();
      this.dispatchEvent(new Event("pause"));
    } else if (event.data === YT.PlayerState.ENDED) {
      this._state.paused = true;
      this._state.ended = true;
      this._stopPolling();
      // Both, in the order a <video> emits them. `pause` is what stops the animation loops in
      // utils.js; `ended` is what tells the UI this is a finished video rather than a paused
      // one, which is the difference between a replay affordance and a play one.
      this.dispatchEvent(new Event("pause"));
      this.dispatchEvent(new Event("ended"));
    }
  }

  _onError() {
    this._stopPolling();
    this._clearSeekTimer();
    this._clearPrimeTimer();
    this._priming = false;
    // Nothing about this player will ever resolve now: duration stays NaN, so anything waiting on
    // metadata (the editor's boot poll, for one) waits forever unless it can find out. Recorded as
    // state *and* announced as an event, in that order, because the event can fire before a later
    // module has attached a listener - a media element's `error` property is what survives that
    // race, and it lets the same check cover a <video> whose file will not load.
    this._state.error = {
      code: MEDIA_ERR_SRC_NOT_SUPPORTED,
      message: "The YouTube player could not play this video.",
    };
    this.dispatchEvent(new Event("error"));
    const videoId = this.dataset.videoId;

    const container = document.createElement("div");
    container.className = "youtube-video-error";

    const message = document.createElement("p");
    message.textContent = "This video can't be played here.";

    const link = document.createElement("a");
    link.href = `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Watch on YouTube";

    container.append(message, link);
    this.replaceChildren(container);
  }

  _startPolling() {
    this._stopPolling();
    this._pollTimer = setInterval(() => {
      this._state.currentTime = this._player.getCurrentTime();
      this._state.loadedFraction = this._player.getVideoLoadedFraction();
      this.dispatchEvent(new Event("timeupdate"));
    }, POLL_INTERVAL_MS);
  }

  _stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  _clearSeekTimer() {
    if (this._seekTimer) {
      clearTimeout(this._seekTimer);
      this._seekTimer = null;
    }
  }

  // Synthesize the seeking/timeupdate/seeked sequence a <video> emits, because the IFrame API
  // emits nothing at all for a seek - and, while paused, nothing at any other time either
  // (`_startPolling` only runs during playback). Without this the whole app keeps painting the
  // time the playhead used to be at: AnnotationPlayer re-applies annotations on `seeked`,
  // BlurEditor's rig and active-point highlight track `timeupdate`/`seeked`, its keyboard nudge
  // is flushed on `seeking`, and the editor's comment rig and scrubber use the same pair. A
  // stale blur box is not a cosmetic problem: it is an editable box drawn for the wrong frame.
  _beginSeek(target) {
    this._clearSeekTimer();
    this._state.seeking = true;
    this.dispatchEvent(new Event("seeking"));

    const startedAt = performance.now();
    const poll = () => {
      this._state.currentTime = this._player.getCurrentTime();
      // Every poll, not just the last one, so a slow seek still repaints within ~50ms instead of
      // holding the old frame's overlays until the whole seek settles.
      this.dispatchEvent(new Event("timeupdate"));

      const arrived =
        Math.abs(this._state.currentTime - target) <= SEEK_TOLERANCE_SECONDS;
      if (!arrived && performance.now() - startedAt < SEEK_TIMEOUT_MS) {
        this._seekTimer = setTimeout(poll, SEEK_POLL_INTERVAL_MS);
        return;
      }
      this._seekTimer = null;
      this._state.seeking = false;
      this.dispatchEvent(new Event("seeked"));
    };
    // Deferred rather than called here, so `seeked` is always asynchronous - a listener attached
    // right after assigning currentTime still sees it, the way it would on a <video>.
    this._seekTimer = setTimeout(poll, SEEK_POLL_INTERVAL_MS);
  }

  _whenReady(fn) {
    if (this._ready) {
      fn();
    } else {
      this._pending.push(fn);
    }
  }

  play() {
    this._playRequested = true;
    this._cancelPriming();
    this._whenReady(() => this._player.playVideo());
  }

  pause() {
    this._whenReady(() => this._player.pauseVideo());
  }

  get currentTime() {
    return this._ready ? this._player.getCurrentTime() : this._state.currentTime;
  }

  set currentTime(time) {
    this._state.currentTime = time;
    this._whenReady(() => {
      this._player.seekTo(time, true);
      this._beginSeek(time);
    });
  }

  get duration() {
    return this._state.duration;
  }

  get paused() {
    return this._state.paused;
  }

  get seeking() {
    return this._state.seeking;
  }

  get ended() {
    return this._state.ended;
  }

  get error() {
    return this._state.error;
  }

  get readyState() {
    // Only ever HAVE_NOTHING or HAVE_METADATA: the IFrame API reports nothing about how much of
    // the video is buffered, so claiming more than "duration and seeking work" would be a guess.
    return this._ready ? HAVE_METADATA : HAVE_NOTHING;
  }

  get muted() {
    // While priming, the player is muted for reasons of its own (see `_primeFirstFrame`), so the
    // app's own intent is the honest answer.
    if (!this._ready || this._priming) return this._state.muted;
    return this._player.isMuted();
  }

  set muted(muted) {
    this._state.muted = muted;
    this._whenReady(() => (muted ? this._player.mute() : this._player.unMute()));
  }

  get volume() {
    return this._state.volume;
  }

  set volume(volume) {
    this._state.volume = volume;
    this._whenReady(() => this._player.setVolume(volume * 100));
  }

  get playbackRate() {
    return this._state.playbackRate;
  }

  set playbackRate(rate) {
    this._state.playbackRate = rate;
    this._whenReady(() => this._player.setPlaybackRate(rate));
  }

  // AnnotationPlayer treats these as a real <video>'s intrinsic size: it feeds
  // them to contentRect() to find where the picture sits inside this element's
  // box and pins the annotation overlay there. The IFrame Player API exposes no
  // getter for the underlying video's resolution, so report a constant 16:9 -
  // the shape of a standard YouTube upload - which is also how the YouTube
  // player itself fits the picture into the iframe, so the overlay lands on it.
  // A non-16:9 video (a Short, say) gets an overlay aligned to a 16:9 frame
  // instead of its real one.
  get videoWidth() {
    return DEFAULT_WIDTH;
  }

  get videoHeight() {
    return DEFAULT_HEIGHT;
  }

  // YouTube's own captions system is separate from the browser-native
  // TextTrack API and isn't implemented here (out of scope for this pass).
  // Returning an empty list keeps AnnotationPlayer.js's unconditional
  // `textTracks.length` read on loadedmetadata from throwing, and correctly
  // hides the captions button since it has nothing to show.
  get textTracks() {
    return [];
  }

  get buffered() {
    const duration = this._state.duration || 0;
    const end = (this._state.loadedFraction || 0) * duration;
    return {
      length: end > 0 ? 1 : 0,
      start: () => 0,
      end: () => end,
    };
  }

  set src(_value) {
    // no-op: the video source is set via the data-video-id attribute
  }
}

customElements.define("youtube-video", YouTubeVideoElement);
