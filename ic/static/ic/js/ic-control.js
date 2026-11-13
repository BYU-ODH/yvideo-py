// The operator's control window. It never decodes video: it drives the output window over a
// BroadcastChannel and shows the state the output reports back.

// Messages: control → output "load", "play", "pause", "seek", "blank", "mute", "subtitles",
// "cueSize", "idle", "ping"; output → control "hello", "state", "ended", "error".
const CHANNEL_NAME = "ic-player";
const OUTPUT_LOST_MS = 2000;
const SEEK_STEP_SECONDS = 5;

function nextPlayableIndex(items, index, direction = 1) {
  let candidate = index + direction;
  while (candidate >= 0 && candidate < items.length) {
    if (items[candidate].playable !== false) return candidate;
    candidate += direction;
  }
  return null;
}

// What happens when an item finishes. Loop items never end (the output loops the
// video), so an "ended" for one is treated as a loop restart.
function afterEnded(items, index) {
  const item = items[index];
  if (!item) return { action: "idle", nextIndex: null };
  if (item.mode === "loop") return { action: "loop", nextIndex: index };
  const nextIndex = nextPlayableIndex(items, index, 1);
  if (item.auto_advance && nextIndex !== null) return { action: "advance", nextIndex };
  return { action: "idle", nextIndex };
}

function clampSeek(time, duration) {
  if (!Number.isFinite(time) || time < 0) return 0;
  if (Number.isFinite(duration) && duration > 0) return Math.min(time, Math.max(0, duration - 0.05));
  return time;
}

function formatClock(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00:00";
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

const LOST_CHECK_MS = 500;

const data = JSON.parse(document.getElementById("ic-page-data").textContent);
const items = data.items;
const channel = new BroadcastChannel(CHANNEL_NAME);

const el = (id) => document.getElementById(id);
const ui = {
  openOutput: el("ic-open-output"),
  outputStatus: el("ic-output-status"),
  lost: el("ic-output-lost"),
  lostText: el("ic-output-lost-text"),
  reopen: el("ic-reopen-output"),
  nowTitle: el("ic-now-title"),
  nowMode: el("ic-now-mode"),
  time: el("ic-time"),
  duration: el("ic-duration"),
  scrubber: el("ic-scrubber"),
  playPause: el("ic-play-pause"),
  prev: el("ic-prev"),
  next: el("ic-next"),
  back: el("ic-back"),
  forward: el("ic-forward"),
  blank: el("ic-blank"),
  mute: el("ic-mute"),
  subtitles: el("ic-subtitles"),
  cueSize: el("ic-cue-size"),
  active: el("ic-active-annotations"),
  upcoming: el("ic-next-annotation"),
  ready: el("ic-ready-next"),
  message: el("ic-message"),
  queue: document.querySelectorAll("[data-ic-load]"),
};

const state = {
  index: null,
  output: null, // last state reported by the output
  lastStateAt: 0,
  connected: false,
  blanked: false,
  muted: false,
  subtitlesOn: true,
  scrubbing: false,
  // False until the first state report: a reloaded controller adopts the output's
  // blank/mute/subtitle settings instead of resetting them.
  synced: false,
};

let outputWindow = null;

function send(type, extra = {}) {
  channel.postMessage({ type, ...extra });
}

function showMessage(text, kind = "info") {
  ui.message.textContent = text || "";
  ui.message.dataset.kind = kind;
  ui.message.hidden = !text;
}

async function otherScreen() {
  if (!("getScreenDetails" in window)) return null;
  try {
    const details = await window.getScreenDetails();
    return details.screens.find((screen) => screen !== details.currentScreen) || null;
  } catch {
    return null;
  }
}

async function openOutput({ reopen = false } = {}) {
  // window.open() on a live "ic-output" window would reload it and stop the film. Only
  // "Reopen" (the output stopped answering) may replace it.
  if (state.connected && !reopen) {
    outputWindow?.focus();
    showMessage("The output is already open.");
    return;
  }
  const screen = await otherScreen();
  const features = screen
    ? `popup,left=${screen.availLeft},top=${screen.availTop},width=${screen.availWidth},height=${screen.availHeight}`
    : "popup,width=1280,height=720";
  outputWindow = window.open(data.output_url, "ic-output", features);
  if (!outputWindow) {
    showMessage("The browser blocked the output window. Allow pop-ups for this page.", "error");
    return;
  }
  showMessage(
    screen
      ? "Output opened on the second screen. If it isn't fullscreen, click it once."
      : "Output opened. Drag it to the projector screen and click it once to fill the screen.",
  );
  outputWindow.addEventListener("load", () => {
    if (screen) {
      outputWindow.document.documentElement
        .requestFullscreen({ screen })
        .catch(() => {});
    }
  });
}

function loadItem(index, { autoplay = false, startAt = 0 } = {}) {
  const item = items[index];
  if (!item) return;
  if (item.playable === false) {
    showMessage(`"${item.title}" can't be played yet: ${item.problems.map((p) => p.message).join(" ")}`, "error");
    return;
  }
  state.index = index;
  ui.ready.hidden = true;
  send("load", { item, autoplay, startAt });
  render();
}

function handleEnded() {
  const decision = afterEnded(items, state.index);
  if (decision.action === "advance") {
    loadItem(decision.nextIndex, { autoplay: true });
  } else if (decision.action === "idle") {
    send("idle");
    ui.ready.hidden = decision.nextIndex === null;
    if (decision.nextIndex !== null) {
      ui.ready.textContent = `Ready: ${items[decision.nextIndex].title} (press N)`;
    }
  }
}

channel.addEventListener("message", (event) => {
  const message = event.data || {};
  if (message.type === "state") {
    state.output = message.state;
    state.lastStateAt = Date.now();
    state.connected = true;
    ui.lost.hidden = true;
    if (!state.synced) {
      state.synced = true;
      state.blanked = message.state.blanked;
      state.muted = message.state.muted;
      state.subtitlesOn = message.state.subtitlesOn;
    }
    // Re-attach after this page reloads while the output keeps playing.
    if (state.index === null && message.state.itemId !== null) {
      const index = items.findIndex((item) => item.id === message.state.itemId);
      if (index !== -1) state.index = index;
    }
    if (!state.scrubbing) render();
  } else if (message.type === "hello") {
    state.connected = true;
    state.lastStateAt = Date.now();
    // Reassert operator settings in a reopened output. Before the first state report
    // the output's own settings win (see state.synced).
    if (!state.synced) return;
    send("blank", { on: state.blanked });
    send("mute", { on: state.muted });
    send("subtitles", { on: state.subtitlesOn });
    send("cueSize", { value: ui.cueSize.value });
  } else if (message.type === "ended") {
    if (message.itemId === items[state.index]?.id) handleEnded();
  } else if (message.type === "error") {
    showMessage(message.message, "error");
  }
});

function isPlaying() {
  return Boolean(state.output && state.output.itemId !== null && !state.output.paused);
}

function render() {
  const output = state.output;
  const item = items[state.index];
  ui.queue.forEach((button) => {
    const isCurrent = Number(button.dataset.icLoad) === state.index;
    button.closest("li").classList.toggle("ic-current", isCurrent);
    button.setAttribute("aria-current", isCurrent ? "true" : "false");
  });
  ui.nowTitle.textContent = item ? item.title : "Nothing loaded";
  ui.nowMode.textContent = item ? (item.mode === "loop" ? "Looping until advanced" : item.auto_advance ? "Plays once, then the next item" : "Plays once") : "";
  const current = Boolean(output && item && output.itemId === item.id);
  const time = current ? output.time : 0;
  const duration = current ? output.duration : null;
  ui.time.textContent = formatClock(time);
  ui.duration.textContent = duration ? formatClock(duration) : "–:––:––";
  ui.scrubber.max = duration || 0;
  if (!state.scrubbing) ui.scrubber.value = time;
  ui.scrubber.disabled = !duration;
  ui.playPause.textContent = isPlaying() ? "Pause" : "Play";
  ui.playPause.disabled = !item;
  ui.blank.setAttribute("aria-pressed", String(state.blanked));
  ui.blank.textContent = state.blanked ? "UNBLANK" : "BLANK";
  ui.mute.setAttribute("aria-pressed", String(state.muted));
  ui.mute.textContent = state.muted ? "Unmute" : "Mute";
  ui.subtitles.setAttribute("aria-pressed", String(state.subtitlesOn));
  ui.subtitles.disabled = !output?.hasSubtitles;
  ui.active.textContent = output?.active?.length ? output.active.join(" · ") : "None";
  ui.upcoming.textContent = output?.next ? `${output.next.label} at ${formatClock(output.next.at)}` : "None";
  ui.outputStatus.textContent = state.connected
    ? output?.fullscreen
      ? "Output connected (fullscreen)"
      : "Output connected (not fullscreen: click the output once)"
    : "Output not open";
  ui.outputStatus.dataset.connected = String(state.connected);
}

function togglePlay() {
  if (state.index === null) {
    const first = nextPlayableIndex(items, -1);
    if (first !== null) loadItem(first, { autoplay: true });
    return;
  }
  if (!state.output || state.output.itemId !== items[state.index].id) {
    loadItem(state.index, { autoplay: true });
    return;
  }
  send(isPlaying() ? "pause" : "play");
}

function step(direction) {
  const start = state.index === null ? -1 : state.index;
  const target = nextPlayableIndex(items, start, direction);
  if (target !== null) loadItem(target, { autoplay: isPlaying() || direction > 0 });
}

function seekBy(delta) {
  if (!state.output) return;
  send("seek", { time: clampSeek(state.output.time + delta, state.output.duration) });
}

function toggleBlank() {
  state.blanked = !state.blanked;
  send("blank", { on: state.blanked });
  render();
}

function toggleMute() {
  state.muted = !state.muted;
  send("mute", { on: state.muted });
  render();
}

function toggleSubtitles() {
  state.subtitlesOn = !state.subtitlesOn;
  send("subtitles", { on: state.subtitlesOn });
  render();
}

ui.openOutput.addEventListener("click", () => openOutput());
ui.reopen.addEventListener("click", async () => {
  const resumeAt = state.output?.time || 0;
  const wasPlaying = isPlaying();
  await openOutput({ reopen: true });
  ui.lost.hidden = true;
  const resume = () => {
    if (state.index !== null) loadItem(state.index, { autoplay: wasPlaying, startAt: resumeAt });
    channel.removeEventListener("message", onHello);
  };
  const onHello = (event) => {
    if (event.data?.type === "hello") resume();
  };
  channel.addEventListener("message", onHello);
});
ui.playPause.addEventListener("click", togglePlay);
ui.prev.addEventListener("click", () => step(-1));
ui.next.addEventListener("click", () => step(1));
ui.back.addEventListener("click", () => seekBy(-SEEK_STEP_SECONDS));
ui.forward.addEventListener("click", () => seekBy(SEEK_STEP_SECONDS));
ui.blank.addEventListener("click", toggleBlank);
ui.mute.addEventListener("click", toggleMute);
ui.subtitles.addEventListener("click", toggleSubtitles);
ui.cueSize.addEventListener("input", () => {
  localStorage.setItem("icCueSize", ui.cueSize.value);
  send("cueSize", { value: ui.cueSize.value });
});
ui.cueSize.value = localStorage.getItem("icCueSize") || ui.cueSize.value;
ui.scrubber.addEventListener("input", () => {
  state.scrubbing = true;
  ui.time.textContent = formatClock(Number(ui.scrubber.value));
});
ui.scrubber.addEventListener("change", () => {
  state.scrubbing = false;
  send("seek", { time: Number(ui.scrubber.value) });
});
ui.queue.forEach((button) => {
  button.addEventListener("click", () => loadItem(Number(button.dataset.icLoad)));
});

const KEYS = {
  " ": togglePlay,
  n: () => step(1),
  p: () => step(-1),
  b: toggleBlank,
  m: toggleMute,
  s: toggleSubtitles,
  o: () => openOutput(),
  ArrowLeft: () => seekBy(-SEEK_STEP_SECONDS),
  ArrowRight: () => seekBy(SEEK_STEP_SECONDS),
};

document.addEventListener("keydown", (event) => {
  if (event.target.closest("input, select, textarea, [contenteditable]")) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const handler = KEYS[event.key] || KEYS[event.key.toLowerCase()];
  if (!handler) return;
  event.preventDefault();
  handler();
});

window.addEventListener("beforeunload", (event) => {
  if (isPlaying()) {
    event.preventDefault();
    event.returnValue = "";
  }
});

setInterval(() => {
  const lost = state.connected && Date.now() - state.lastStateAt > OUTPUT_LOST_MS;
  if (lost) {
    state.connected = false;
    ui.lost.hidden = false;
    ui.lostText.textContent = `Output lost${state.output ? ` at ${formatClock(state.output.time)}` : ""}.`;
    render();
  }
}, LOST_CHECK_MS);

// The booth's sync badge refreshes itself; when it reports data applied after this page
// was built, the queue may be out of date.
if (data.booth) {
  const stale = el("ic-stale");
  el("ic-reload").addEventListener("click", () => window.location.reload());
  document.body.addEventListener("htmx:afterSwap", () => {
    const marker = document.querySelector("[data-ic-data-version]");
    if (marker && marker.dataset.icDataVersion !== data.data_version) stale.hidden = false;
  });
}

// An output that is already open (e.g. this page was reloaded) answers the ping.
send("ping");
render();
window.icControl = { state, items, loadItem, send };
