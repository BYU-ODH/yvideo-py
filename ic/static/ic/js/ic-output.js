// The patron-facing output window. It plays what the control window tells it to, using
// the same AnnotationPlayer as every other Y-video page, and reports its state back.
//
// Like player-wrapper.js, this file only uses AnnotationPlayer's public API.

import { AnnotationPlayer } from "../../js/AnnotationPlayer.js";
import {
  CHANNEL_NAME,
  CMD,
  EVENT,
  MODE,
  STATE_INTERVAL_MS,
  annotationsAt,
  clampSeek,
  describeAnnotation,
  nextAnnotationAfter,
} from "./ic-protocol.js";

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
  error: null,
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
  state.error = null;
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
    video.loop = item.mode === MODE.LOOP;
    player.setVideoSource(item.media_url);
    video.addEventListener(
      "loadedmetadata",
      () => {
        if (startAt > 0) player.setCurrentTime(clampSeek(startAt, video.duration));
        applySubtitleVisibility();
        if (autoplay) play();
      },
      { once: true },
    );
  } catch (error) {
    state.error = String(error);
    post(EVENT.ERROR, { itemId: item.id, message: state.error });
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
  [CMD.LOAD]: load,
  [CMD.PLAY]: play,
  [CMD.PAUSE]: () => player.pause(),
  [CMD.SEEK]: ({ time }) => player.setCurrentTime(clampSeek(time, video.duration)),
  [CMD.BLANK]: ({ on }) => setBlank(on),
  [CMD.MUTE]: ({ on }) => setMute(on),
  [CMD.SUBTITLES]: ({ on }) => {
    state.subtitlesOn = Boolean(on);
    applySubtitleVisibility();
  },
  [CMD.CUE_SIZE]: ({ value }) => {
    document.documentElement.style.setProperty("--ic-cue-size", `${Number(value) || 4}vh`);
  },
  [CMD.IDLE]: () => {
    video.pause();
    state.item = null;
    showIdle(true);
  },
  [CMD.PING]: () => post(EVENT.HELLO),
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
  post(EVENT.ENDED, { itemId: state.item?.id });
  showIdle();
});
video.addEventListener("error", () => {
  if (!state.item) return;
  state.error = `The video could not be played (${video.error?.message || "media error"}).`;
  post(EVENT.ERROR, { itemId: state.item.id, message: state.error });
});

function snapshot() {
  const time = video.currentTime || 0;
  const next = nextAnnotationAfter(state.annotations, time);
  return {
    itemId: state.item?.id ?? null,
    time,
    duration: Number.isFinite(video.duration) ? video.duration : null,
    paused: video.paused,
    ended: video.ended,
    blanked: state.blanked,
    muted: state.muted,
    subtitlesOn: state.subtitlesOn,
    hasSubtitles: video.textTracks.length > 0,
    readyState: video.readyState,
    fullscreen: Boolean(document.fullscreenElement),
    error: state.error,
    active: annotationsAt(state.annotations, time).map(describeAnnotation),
    next: next ? { at: next.start, label: describeAnnotation(next) } : null,
  };
}

setInterval(() => post(EVENT.STATE, { state: snapshot() }), STATE_INTERVAL_MS);

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
post(EVENT.HELLO);
