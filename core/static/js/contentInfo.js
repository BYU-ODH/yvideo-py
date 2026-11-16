import { getCSRFToken, displayUserMessage } from "./utils.js";

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
      // A plain-text body is a message written for the user (e.g. "scheduled in an IC
      // program"); anything else is an error page and gets the generic message.
      const isMessage = (deleteResponse.headers.get("content-type") ?? "").startsWith("text/plain");
      displayUserMessage("error", isMessage ? (await deleteResponse.text()) : "Failed to delete content from playlist");
      return;
    }
    window.location.replace(`/playlists/${playlistIdInput.value}/`);
  });
}

function initialize() {
  setupDeleteFromPlaylist();
}

initialize();
