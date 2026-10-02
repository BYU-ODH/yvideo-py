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

/* makePostRequest
* handles csrf token and conent type placement. Returns the result of the request.
*/
export async function makePostRequest(URL, body, contentType = "application/json") {
  return fetch(URL, {
    method: "post",
    headers: {
      "X-CSRFToken": getCSRFToken(),
      "Content-Type": contentType
    },
    body: body
  });
}

/* setupInPlaceFieldEdit
* May be used to edit any field that can be represented by an input or textarea element.
* This must be used with a button that switches the value's display and edit modes.
* The new input is provided to the endpointURL as JSON with the structure {"new_value": VALUE}.
* postEditCallback is provided to enable whatever after effects may be useful after a
* successful edit. postEditCallback is not called on failed edits.
*/
export function setupInPlaceFieldEdit(wrapperElement, editEndpointURL, postEditCallback = null) {
  const titleElement = wrapperElement.querySelector("p, h1, h2, h3, h4, h5, h6, span");
  const inputElement = wrapperElement.querySelector("input, textarea");
  const editButton = wrapperElement.querySelector("button");

  const missingTitle = !titleElement;
  const missingInput = !inputElement;
  const missingButton = !editButton;
  if (missingTitle || missingInput || missingButton) {
    let reason = "Missing the following:";
    if (missingTitle) reason += " title";
    if (missingInput) reason += " input";
    if (missingButton) reason += " button";
    console.error(`Cannot set up in-place field edit. ${reason}.`);
    return;
  }

  function hideInput() {
    inputElement.classList.add("hidden");
    titleElement.classList.remove("hidden");
    editButton.classList.remove("hidden");
  }

  editButton.addEventListener("click", () => {
    titleElement.classList.add("hidden");
    editButton.classList.add("hidden");
    inputElement.classList.remove("hidden");
    inputElement.select();
  });

  inputElement.addEventListener("keydown", async (e) => {
    const key = e.key;
    const originalTitle = titleElement.innerText;
    const newValue = inputElement.value;
    if (key == "Enter") {
      const body = JSON.stringify({
        new_value: newValue
      });
      const response = await makePostRequest(editEndpointURL, body);
      if (response.ok) {
        titleElement.innerText = newValue;
        postEditCallback();
      }
      else {
        inputElement.value = originalTitle;
        titleElement.innerText = originalTitle;
        console.error(`Failed to update title: ${response.error}`);
      }
      hideInput();
    }
    else if (key == "Escape") {
      inputElement.value = originalTitle;
      hideInput();
    }
  });
}
