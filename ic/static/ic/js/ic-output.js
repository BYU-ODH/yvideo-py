// The patron-facing output window. It plays what the control window tells it to, using
// the same AnnotationPlayer as every other Y-video page, and reports its state back.
//
// Like player-wrapper.js, this file only uses AnnotationPlayer's public API.

import { AnnotationPlayer } from "../../js/AnnotationPlayer.js";

// Messages: see ic-control.js.
const CHANNEL_NAME = "ic-player";
const STATE_INTERVAL_MS = 250;

function clampSeek(time, duration) {
  if (!Number.isFinite(time) || time < 0) return 0;
  if (Number.isFinite(duration) && duration > 0) return Math.min(time, Math.max(0, duration - 0.05));
  return time;
}

function describeAnnotation(annotation) {
  const type = annotation.type === "screenBlur" ? "blur" : annotation.type;
  const label = annotation.label || annotation.name || "";
  return label ? `${type}: ${label}` : type;
}

const IDLE_LOCKUP_DELAY_MS = 1000;
const ALL_CONTROLS = [
  "playPauseBtn",
  "volume",
  "scrubber",
  "playTime",
  "speedBtn",
  "clipsBtn",
  "captionsBtn",
  "subtitleSidebarBtn",
  "fullscreenBtn",
];

const container = document.getElementById("annotation-player-container");
const video = document.getElementById("video-player");
const idle = document.getElementById("ic-idle");
const blankOverlay = document.getElementById("ic-blank");
const hint = document.getElementById("ic-fullscreen-hint");

const channel = new BroadcastChannel(CHANNEL_NAME);
const player = new AnnotationPlayer({
  container,
  video,
  disabledControls: ALL_CONTROLS,
  tracks: [],
  subtitleSidebar: false,
  allowFastPlayback: false,
  allowCaptions: true,
});
window.icOutputPlayer = player;

const state = {
  item: null,
  annotations: [],
  blanked: false,
  muted: false,
  subtitlesOn: true,
};
let idleTimer = null;

function post(type, extra = {}) {
  channel.postMessage({ type, ...extra });
}

function showIdle(immediately = false) {
  clearTimeout(idleTimer);
  container.classList.add("ic-hidden");
  const reveal = () => idle.classList.add("ic-visible");
  if (immediately) reveal();
  else idleTimer = setTimeout(reveal, IDLE_LOCKUP_DELAY_MS);
}

function hideIdle() {
  clearTimeout(idleTimer);
  idle.classList.remove("ic-visible");
  container.classList.remove("ic-hidden");
}

function applySubtitleVisibility() {
  Array.from(video.textTracks).forEach((track) => {
    track.mode = state.subtitlesOn ? "showing" : "hidden";
  });
}

async function load({ item, autoplay = false, startAt = 0 }) {
  state.item = item;
  showIdle(true);
  video.pause();
  try {
    const response = await fetch(item.player_data_url, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`player data: HTTP ${response.status}`);
    const data = await response.json();
    if (state.item !== item) return; // a newer load superseded this one
    state.annotations = data.annotations || [];
    player.loadData({
      annotations: state.annotations,
      clips: [],
      subtitles: data.subtitleTracks || [],
    });
    if (!data.media_url) throw new Error("player data has no media URL");
    video.loop = item.mode === "loop";
    // The URL comes with the annotations it belongs to. On the booth it names one verified
    // file, so a sync that lands while this item plays can't change what it streams.
    player.setVideoSource(data.media_url);
    video.addEventListener(
      "loadedmetadata",
      () => {
        if (state.item !== item) return; // a newer load superseded this one
        if (startAt > 0) player.setCurrentTime(clampSeek(startAt, video.duration));
        applySubtitleVisibility();
        if (autoplay) play();
      },
      { once: true },
    );
  } catch (error) {
    post("error", { itemId: item.id, message: String(error) });
  }
}

function play() {
  hideIdle();
  player.play();
}

function setBlank(on) {
  state.blanked = Boolean(on);
  blankOverlay.hidden = !state.blanked;
}

function setMute(on) {
  state.muted = Boolean(on);
  if (state.muted) player.mute(false);
  else if (!player.isMuteAnnotationActive) player.unmute(false);
}

// The operator's mute must hold even when a mute annotation ends and the player unmutes.
video.addEventListener("volumechange", () => {
  if (state.muted && !video.muted) video.muted = true;
});

const handlers = {
  load: load,
  play: play,
  pause: () => player.pause(),
  seek: ({ time }) => player.setCurrentTime(clampSeek(time, video.duration)),
  blank: ({ on }) => setBlank(on),
  mute: ({ on }) => setMute(on),
  subtitles: ({ on }) => {
    state.subtitlesOn = Boolean(on);
    applySubtitleVisibility();
  },
  cueSize: ({ value }) => {
    document.documentElement.style.setProperty("--ic-cue-size", `${Number(value) || 4}vh`);
  },
  idle: () => {
    video.pause();
    state.item = null;
    showIdle(true);
  },
  ping: () => post("hello"),
};

channel.addEventListener("message", (event) => {
  const handler = handlers[event.data?.type];
  if (handler) handler(event.data);
});

video.addEventListener("playing", hideIdle);
// The hint is for the operator setting up; patrons must never see it over a film, so it
// goes the moment playback is requested, before the first frame.
video.addEventListener("play", () => {
  hint.hidden = true;
});
video.addEventListener("ended", () => {
  post("ended", { itemId: state.item?.id });
  showIdle();
});
video.addEventListener("error", () => {
  if (!state.item) return;
  const message = `The video could not be played (${video.error?.message || "media error"}).`;
  post("error", { itemId: state.item.id, message });
});

function snapshot() {
  const time = video.currentTime || 0;
  const annotations = state.annotations || [];
  const next = annotations
    .filter((a) => a.start > time)
    .reduce((soonest, a) => (soonest && soonest.start <= a.start ? soonest : a), null);
  return {
    itemId: state.item?.id ?? null,
    time,
    duration: Number.isFinite(video.duration) ? video.duration : null,
    paused: video.paused,
    blanked: state.blanked,
    muted: state.muted,
    subtitlesOn: state.subtitlesOn,
    hasSubtitles: video.textTracks.length > 0,
    fullscreen: Boolean(document.fullscreenElement),
    active: annotations.filter((a) => a.start <= time && time < a.end).map(describeAnnotation),
    next: next ? { at: next.start, label: describeAnnotation(next) } : null,
  };
}

setInterval(() => post("state", { state: snapshot() }), STATE_INTERVAL_MS);

// Fullscreen needs a user gesture inside this window. The control window tries to place
// this window on the projector screen; one click here (or any key) fills it.
function goFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen?.().catch(() => {});
  }
}
document.addEventListener("click", goFullscreen);
document.addEventListener("keydown", goFullscreen);
document.addEventListener("fullscreenchange", () => {
  if (document.fullscreenElement) hint.hidden = true;
});
setTimeout(() => {
  hint.hidden = true;
}, 8000);

showIdle(true);
post("hello");
