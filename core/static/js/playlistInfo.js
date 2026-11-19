import { getCSRFToken, displayUserMessage, setupInPlaceFieldEdit } from "./utils.js";
import { getPlaylistIdValue } from "./utils.js";

function setupVideoSearch() {
  const searchInput = document.getElementById("video-search");
  const videoList = document.getElementById("playlist-video-list");
  searchInput.addEventListener("input", () => {
    const searchText = searchInput.value.toLowerCase();
    const videos = videoList.querySelectorAll(".playlist-item-tile");
    for (const video of videos) {
      const title = video.querySelector(".video-item-title")?.innerText.toLowerCase() || "";
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
      // A plain-text body is a message written for the user (e.g. "scheduled in an IC
      // program"); anything else is an error page and gets the generic message.
      const isMessage = (deleteResponse.headers.get("content-type") ?? "").startsWith("text/plain");
      displayUserMessage("error", isMessage ? (await deleteResponse.text()) : "Failed to delete playlist");
    } else {
      window.location.replace("/playlists/");
    }
  });
}

function setupPlaylistSettings() {
  const playlistContent = document.getElementById("playlist-content");
  if (!playlistContent) return;
  playlistContent.addEventListener("change", (e) => {
    e.preventDefault();
    console.log("Caught!");
  });
}

function initialize() {
  setupVideoSearch();
  setupDeletePlaylist();
  setupPlaylistSettings();
  const playlistNameWrapper = document.getElementById("playlist-name-wrapper");
  const playlistId = getPlaylistIdValue();
  if (playlistNameWrapper && playlistId !== undefined) {
    setupInPlaceFieldEdit(playlistNameWrapper, `/playlists/${playlistId}/change-name/`, {
      onSuccess: () => {
        const playlistTitle = playlistNameWrapper.querySelector("h3");
        const playlistInput = playlistNameWrapper.querySelector("input");
        if (!playlistTitle || !playlistInput) {
          displayUserMessage("error", "Failed to display updated playlist name. Please refresh the page.");
          return;
        }
        playlistTitle.textContent = playlistInput.value;
      }
    });
  }
}

initialize();
