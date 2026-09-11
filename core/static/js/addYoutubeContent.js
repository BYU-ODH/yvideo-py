import { getCSRFToken } from "./utils.js";

function setupAddYoutubeVideoForm() {
  const form = document.getElementById("add-youtube-video-form");
  if (!form) {
    return;
  }
  const errorEl = document.getElementById("add-youtube-video-error");
  const submitButton = document.getElementById("add-youtube-video-submit");

  function showError(message) {
    errorEl.textContent = message;
    // The element is aria-live, so revealing it here is what announces the failure. Moving
    // focus there instead would take it off the field the user still has to fix.
    errorEl.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    errorEl.hidden = true;
    submitButton.disabled = true;

    const playlistIdInput = document.getElementById("add-youtube-video-playlist-id");
    const titleInput = document.getElementById("add-youtube-video-title");
    const urlInput = document.getElementById("add-youtube-video-url");

    try {
      const response = await fetch(
        `/playlists/${playlistIdInput.value}/content/create-from-url/`, {
          method: "POST",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            "title": titleInput.value,
            "url": urlInput.value
          })
        });

      if (!response.ok) {
        const message = await response.text();
        console.error("Failed to add YouTube video");
        showError(message || "Failed to add YouTube video. Please try again.");
        return;
      }

      window.location.reload();
    } catch (error) {
      // A rejected fetch is the offline//DNS/blocked case, which has no response to read a
      // message out of. Without this it surfaced only as an unhandled rejection in the console:
      // the dialog just sat there, re-enabled, looking like nothing had been submitted.
      console.error("Failed to add YouTube video", error);
      showError("Could not reach the server. Check your connection and try again.");
    } finally {
      submitButton.disabled = false;
    }
  });
}

function initialize() {
  setupAddYoutubeVideoForm();
}

initialize();
