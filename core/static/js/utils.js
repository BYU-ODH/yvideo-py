// This file is not intended to be imported into an html page,
// use this as a module to extend functionality of other js scripts

export function formatSecondsToString(timeInSeconds) {
  const numericTime = Number(timeInSeconds);
  const safeTime = Number.isFinite(numericTime) ? Math.max(0, numericTime) : 0;
  // Rounded before it is split apart, so 59.999 reaches the next minute instead of reading 0:00:60.00.
  const seconds = Number(safeTime.toFixed(2));

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = (seconds % 60).toFixed(2).padStart(5, "0");
  return `${hours}:${String(minutes).padStart(2, "0")}:${remainder}`;
}

export function parseTimeStringToSeconds(time) {
  const parts = String(time).trim().split(":");
  if (parts.length > 3 || parts.some((part) => part.trim() === "")) return NaN;
  return parts.reduce((seconds, part) => seconds * 60 + Number(part), 0);
}

export function createElementFromHTMLString(html, nodeIndex=0) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return template.content.children[nodeIndex];
}

export function applyRect(element, rect) {
  element.style.left = `${rect.x}%`;
  element.style.top = `${rect.y}%`;
  element.style.width = `${rect.width}%`;
  element.style.height = `${rect.height}%`;
}

// Calls `onFrame` on every animation frame while `video` is playing, so UI
// bound to playback (scrubbers, progress bars) moves smoothly instead of
// lurching between the infrequent `timeupdate` events. `onFrame` also runs once
// when playback stops, to settle on the exact final position. Returns a cleanup
// function that detaches the listeners and cancels any pending frame.
export function animateDuringPlayback(video, onFrame) {
  let rafId = null;

  const step = () => {
    onFrame();
    rafId = requestAnimationFrame(step);
  };
  const start = () => {
    if (rafId === null) {
      rafId = requestAnimationFrame(step);
    }
  };
  const stop = () => {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    onFrame();
  };

  video.addEventListener("play", start);
  video.addEventListener("pause", stop);
  video.addEventListener("ended", stop);
  if (!video.paused) {
    start();
  }

  return () => {
    video.removeEventListener("play", start);
    video.removeEventListener("pause", stop);
    video.removeEventListener("ended", stop);
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  };
}

export function getPlaylistIdValue() {
  const settingsEl = document.getElementById("playlist-settings");
  const idValue = settingsEl?.dataset.playlistId;
  if (!idValue) {
    console.error("Failed to get playlist id from the settings panel");
    return;
  }
  return idValue;
}

export function getCSRFToken() {
  const cookieValue = document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1];
  if (!cookieValue) {
    console.error("Unable to get csrftoken from cookie");
    return;
  }
  return cookieValue;
}

// Render and display a user message. A message cannot be empty
// and if the level is not a valid value, the resulting message will be
// an error type message.
export async function displayUserMessage(level, message) {
  if (!message.trim()) {
    console.error("Django Messages cannot be empty");
    return;
  }

  if (level?.toLowerCase() === "error") {
    console.error(message);
  }

  const container = document.getElementById("django-messages-container");
  if (!container) {
    console.error("The Django messages container is missing.");
    return;
  }

  const url = new URL("/messages/render/", window.location.origin);
  url.searchParams.set("message", message);
  url.searchParams.set("level", level);

  const response = await fetch(url, {
    headers: { Accept: "text/html" },
  });
  if (!response.ok || response.redirected) {
    console.log("Failed to render user message.");
    return;
  }

  const html = await response.text();
  if (html === "") {
    console.error("Received empty HTML while rendering a user message.");
    return;
  }

  container.insertAdjacentHTML("beforeend", html);
}
