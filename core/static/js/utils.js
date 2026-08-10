// This file is not intended to be imported into an html page,
// use this as a module to extend functionality of other js scripts
export function formatSecondsToString(timeInSeconds, shouldGiveTimestamp=false) {
  let time;
  if (shouldGiveTimestamp) {
    time = Number(timeInSeconds).toFixed(2);
  } else {
    time = Math.round(timeInSeconds);
  }
  const hours = Math.floor(time / 3600);
  const minutes = Math.floor((time % 3600) / 60);
  const seconds = (time % 60).toFixed(0);
  let decimal = "";

  if (shouldGiveTimestamp) {
    decimal = '.' + Math.round(((time * 100) % 100)).toString().padStart(2, '0');
  }

  return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}${decimal}`;
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
