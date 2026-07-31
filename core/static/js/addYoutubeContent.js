import { getCSRFToken } from "./utils.js";

function setupAddYoutubeVideoForm() {
  const form = document.getElementById("add-youtube-video-form");
  if (!form) {
    return;
  }
  const errorEl = document.getElementById("add-youtube-video-error");
  const submitButton = document.getElementById("add-youtube-video-submit");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    errorEl.hidden = true;
    submitButton.disabled = true;

    const playlistIdInput = document.getElementById("add-youtube-video-playlist-id");
    const titleInput = document.getElementById("add-youtube-video-title");
    const urlInput = document.getElementById("add-youtube-video-url");

    try {
      const response = await fetch("/content/create-from-url/", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          "playlist_id": playlistIdInput.value,
          "title": titleInput.value,
          "url": urlInput.value
        })
      });

      if (!response.ok) {
        const message = await response.text();
        console.error("Failed to add YouTube video");
        errorEl.textContent = message || "Failed to add YouTube video. Please try again.";
        errorEl.hidden = false;
        return;
      }

      window.location.reload();
    } finally {
      submitButton.disabled = false;
    }
  });
}

function initialize() {
  setupAddYoutubeVideoForm();
}

initialize();
