// The message protocol between the IC control window and the output window, and the pure
// sequencing rules both sides follow. No DOM access here, so tests/js can exercise it in node.

export const CHANNEL_NAME = "ic-player";

// Control → output.
export const CMD = Object.freeze({
  LOAD: "load",
  PLAY: "play",
  PAUSE: "pause",
  SEEK: "seek",
  BLANK: "blank",
  MUTE: "mute",
  SUBTITLES: "subtitles",
  CUE_SIZE: "cueSize",
  IDLE: "idle",
  PING: "ping",
});

// Output → control.
export const EVENT = Object.freeze({
  HELLO: "hello",
  STATE: "state",
  ENDED: "ended",
  ERROR: "error",
});

export const STATE_INTERVAL_MS = 250;
export const OUTPUT_LOST_MS = 2000;
export const SEEK_STEP_SECONDS = 5;

export const MODE = Object.freeze({ ONCE: "once", LOOP: "loop" });

// What happens when an item finishes. LOOP items never end (the output loops the video),
// so an "ended" for one is treated as a loop restart.
export function afterEnded(items, index) {
  const item = items[index];
  if (!item) return { action: "idle", nextIndex: null };
  if (item.mode === MODE.LOOP) return { action: "loop", nextIndex: index };
  const nextIndex = nextPlayableIndex(items, index, 1);
  if (item.auto_advance && nextIndex !== null) {
    return { action: "advance", nextIndex };
  }
  return { action: "idle", nextIndex };
}

export function nextPlayableIndex(items, index, direction = 1) {
  let candidate = index + direction;
  while (candidate >= 0 && candidate < items.length) {
    if (items[candidate].playable !== false) return candidate;
    candidate += direction;
  }
  return null;
}

export function firstPlayableIndex(items) {
  return nextPlayableIndex(items, -1, 1);
}

export function clampSeek(time, duration) {
  if (!Number.isFinite(time) || time < 0) return 0;
  if (Number.isFinite(duration) && duration > 0) return Math.min(time, Math.max(0, duration - 0.05));
  return time;
}

export function annotationsAt(annotations, time) {
  return (annotations || []).filter((a) => a.start <= time && time < a.end);
}

export function nextAnnotationAfter(annotations, time) {
  const upcoming = (annotations || [])
    .filter((a) => a.start > time)
    .sort((a, b) => a.start - b.start);
  return upcoming[0] || null;
}

export function describeAnnotation(annotation) {
  const type = annotation.type === "screenBlur" ? "blur" : annotation.type;
  const label = annotation.label || annotation.name || "";
  return label ? `${type}: ${label}` : type;
}

export function formatClock(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00:00";
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function outputLost(lastStateAt, now, connected) {
  return Boolean(connected) && now - lastStateAt > OUTPUT_LOST_MS;
}
