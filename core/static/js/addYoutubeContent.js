import { getCSRFToken } from "./utils.js";

function setupAddYoutubeVideoForm() {
  const form = document.getElementById("add-youtube-video-form");
  if (!form) {
    return;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const playlistIdInput = document.getElementById("add-youtube-video-playlist-id");
    const titleInput = document.getElementById("add-youtube-video-title");
    const urlInput = document.getElementById("add-youtube-video-url");

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
      console.error("Failed to add YouTube video");
      return;
    }

    window.location.reload();
  });
}

function initialize() {
  setupAddYoutubeVideoForm();
}

initialize();
