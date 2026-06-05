function setupRemoveFromCollection() {
  const confirmRemoveButton = document.getElementById("content-confirm-remove");
  const contentIdInput = document.getElementById("content-id-input");
  if (!confirmRemoveButton || !contentIdInput) {
    console.error("confirmRemoveButton and/or contentIdInput were undefined");
    return;
  }
  confirmRemoveButton.addEventListener("click", async () => {
    const removeResponse = await fetch(`/content/remove-from-collection/${contentIdInput.value}/`)
    if (!removeResponse.ok) {
      console.error("Failed to remove content from collection");
      return;
    }
    const collectionId = await removeResponse.text();
    window.location.replace(`/collections/${collectionId}/`);
  });
}

function initialize() {
  setupRemoveFromCollection();
}

initialize();
