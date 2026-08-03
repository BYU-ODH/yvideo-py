// A custom element that wraps the YouTube IFrame Player API and exposes the
// small subset of the HTMLVideoElement surface that AnnotationPlayer.js
// actually touches (currentTime/duration/paused/muted/volume/playbackRate,
// play()/pause(), textTracks/buffered stubs, and the events AnnotationPlayer
// listens for). This lets AnnotationPlayer treat a YouTube embed exactly like
// a <video> element without any changes to AnnotationPlayer.js itself.

const IFRAME_API_SRC = "https://www.youtube.com/iframe_api";
const POLL_INTERVAL_MS = 250;
const DEFAULT_WIDTH = 1280;
const DEFAULT_HEIGHT = 720;

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
      videoWidth: 0,
      videoHeight: 0,
    };
    this._player = null;
    this._ready = false;
    this._pending = [];
    this._pollTimer = null;
    this._initialized = false;
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
    // The IFrame Player API has no getter for the underlying video's
    // intrinsic resolution, so this reports the element's own rendered box
    // (forced to 16:9 by AnnotationPlayer.css) rather than the true video
    // dimensions. Fine for standard 16:9 videos; non-16:9 videos (e.g.
    // Shorts) won't get the same aspect-correct wrapper fit that file-backed
    // <video> content gets from its real videoWidth/videoHeight.
    const rect = this.getBoundingClientRect();
    this._state.videoWidth = rect.width || DEFAULT_WIDTH;
    this._state.videoHeight = rect.height || DEFAULT_HEIGHT;

    this._player.setVolume(this._state.volume * 100);
    if (this._state.muted) {
      this._player.mute();
    }

    const pending = this._pending;
    this._pending = [];
    pending.forEach((fn) => fn());

    this.dispatchEvent(new Event("loadedmetadata"));
  }

  _onStateChange(event) {
    const YT = window.YT;
    if (event.data === YT.PlayerState.PLAYING) {
      this._state.paused = false;
      this._startPolling();
      this.dispatchEvent(new Event("play"));
      this.dispatchEvent(new Event("playing"));
    } else if (event.data === YT.PlayerState.PAUSED) {
      this._state.paused = true;
      this._stopPolling();
      this.dispatchEvent(new Event("pause"));
    } else if (event.data === YT.PlayerState.ENDED) {
      this._state.paused = true;
      this._stopPolling();
      this.dispatchEvent(new Event("pause"));
    }
  }

  _onError() {
    this._stopPolling();
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

  _whenReady(fn) {
    if (this._ready) {
      fn();
    } else {
      this._pending.push(fn);
    }
  }

  play() {
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
    this._whenReady(() => this._player.seekTo(time, true));
  }

  get duration() {
    return this._state.duration;
  }

  get paused() {
    return this._state.paused;
  }

  get muted() {
    return this._ready ? this._player.isMuted() : this._state.muted;
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

  get videoWidth() {
    return this._state.videoWidth || DEFAULT_WIDTH;
  }

  get videoHeight() {
    return this._state.videoHeight || DEFAULT_HEIGHT;
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
