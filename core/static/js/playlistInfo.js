import { getCSRFToken } from "./utils.js";
import { getPlaylistIdValue } from "./utils.js";

function setupVideoSearch() {
  const searchInput = document.getElementById("video-search");
  const videoList = document.getElementById("playlist-video-list");
  searchInput.addEventListener("input", () => {
    const searchText = searchInput.value.toLowerCase();
    const videos = videoList.querySelectorAll(".playlist-video");
    for (const video of videos) {
      const title = video.querySelector(".video-title")?.innerText.toLowerCase() || "";
      video.style.display = title.includes(searchText) ? "" : "none";
    }
  });
}

function setupDeletePlaylist() {
  const deleteButton = document.getElementById("playlist-confirm-delete");
  if (!deleteButton) {
    return;
  }
  const idValue = getPlaylistIdValue();
  deleteButton.addEventListener("click", async () => {
    if (idValue === undefined) {
      deleteButton.disabled = true;
      deleteButton.classList.add("disabled");
      return;
    }
    const deleteResponse = await fetch(`/playlists/${idValue}/delete/`, {
      method: "DELETE",
      headers: {
        "X-CSRFToken": getCSRFToken()
      }
    });
    if (!deleteResponse.ok) {
      console.error("Failed to delete playlist");
    } else {
      window.location.replace("/playlists/");
    }
  });
}

function setupResetPlaylistSettings() {
  const resetButton = document.getElementById("playlist-settings-reset");
  if (!resetButton) {
    return;
  }
  resetButton.addEventListener("click", async (event) => {
    event.preventDefault();
    const playlistId = getPlaylistIdValue();
    const resetResponse = await fetch(`/playlists/${playlistId}/settings/`);
    if (!resetResponse.ok) {
      console.error("Failed to reset playlist settings");
      return;
    }
    const settingsHTML = await resetResponse.text();
    const currentSettingsEl = document.getElementById("playlist-settings");
    currentSettingsEl.outerHTML = settingsHTML;
    setupPlaylistSettings();
  });
}

function setupPlaylistSettings() {
  setupDeletePlaylist();
  setupResetPlaylistSettings();
}

function initialize() {
  setupVideoSearch();
  setupPlaylistSettings();
}

initialize();
