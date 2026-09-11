import { getCSRFToken } from "./utils.js";

function setupDeleteFromPlaylist() {
  const confirmDeleteButton = document.getElementById("content-confirm-delete");
  const contentIdInput = document.getElementById("content-id-input");
  const playlistIdInput = document.getElementById("playlist-id-input");
  if (!confirmDeleteButton || !contentIdInput || !playlistIdInput) {
    console.error("confirmDeleteButton, contentIdInput and/or playlistIdInput were undefined");
    return;
  }
  confirmDeleteButton.addEventListener("click", async () => {
    const deleteResponse = await fetch(`/content/${contentIdInput.value}/delete/`, {
      method: "DELETE",
      headers: { "X-CSRFToken": getCSRFToken() },
    });
    if (!deleteResponse.ok) {
      console.error("Failed to delete content from playlist");
      return;
    }
    window.location.replace(`/playlists/${playlistIdInput.value}/`);
  });
}

function initialize() {
  setupDeleteFromPlaylist();
}

initialize();
