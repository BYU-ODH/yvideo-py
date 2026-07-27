function setupRemoveFromPlaylist() {
  const confirmRemoveButton = document.getElementById("content-confirm-remove");
  const contentIdInput = document.getElementById("content-id-input");
  if (!confirmRemoveButton || !contentIdInput) {
    console.error("confirmRemoveButton and/or contentIdInput were undefined");
    return;
  }
  confirmRemoveButton.addEventListener("click", async () => {
    const removeResponse = await fetch(`/content/remove-from-playlist/${contentIdInput.value}/`)
    if (!removeResponse.ok) {
      console.error("Failed to remove content from playlist");
      return;
    }
    const playlistId = await removeResponse.text();
    window.location.replace(`/playlists/${playlistId}/`);
  });
}

function initialize() {
  setupRemoveFromPlaylist();
}

initialize();
