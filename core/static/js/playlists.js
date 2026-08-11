import { getCSRFToken } from "./utils.js";

function setupPlaylistSearch() {
  const playlistSearch = document.getElementById("playlists-search");
  if (!playlistSearch) {
    return;
  }
  playlistSearch.addEventListener("input", () => {
    const searchText = playlistSearch.value.toLowerCase();
    const landingPageList = playlistSearch.closest(".landing-page-playlist-list");
    const playlists = landingPageList.querySelectorAll(".landing-page-playlist");
    for (let playlist of playlists) {
      const name = playlist.querySelector(".playlist-header-name")?.innerText.toLowerCase() || "";
      playlist.style.display = name.includes(searchText) ? "" : "none";
    }
  });
}

function setupNewPlaylistSubmit() {
  const form = document.getElementById("new-playlist-form");
  if (!form) {
    return;
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const nameInput = form.querySelector("#new-playlist-name-input");
    const response = await fetch("/playlists/create/", {
      method: "POST",
      headers: {
        "X-CSRFToken": getCSRFToken(),
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        name: nameInput.value
      })
    });
    if (!response.ok) {
      console.error("Failed to save new playlist");
      return;
    }
    location.reload();
  });
}

function initialize() {
  setupPlaylistSearch();
  setupNewPlaylistSubmit();
}

initialize();
