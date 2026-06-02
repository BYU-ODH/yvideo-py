import { getCSRFToken } from "./utils.js";

function setupVideoSearch() {
  const searchInput = document.getElementById("video-search");
  const parent = searchInput.closest("#collection-videos-content");
  const videoList = parent.querySelector("#collection-video-list");
  searchInput.addEventListener("input", () => {
    const searchText = searchInput.value.toLowerCase();
    const videos = videoList.querySelectorAll(".collection-video");
    for (const video of videos) {
      const title = video.querySelector(".video-title")?.innerText.toLowerCase() || "";
      video.style.display = title.includes(searchText) ? "" : "none";
    }
  });
}

function getCollectionIdValue() {
  const collectionForm = document.getElementById("collection-settings-form");
  const idInput = collectionForm.querySelector("input[name='id']");
  const idValue = idInput?.value;
  if (idValue === undefined) {
    console.error("Failed to get collection id from form");
    return;
  }
  return idValue;
}

function setupDeleteCollection() {
  const deleteButton = document.getElementById("collection-confirm-delete");
  const idValue = getCollectionIdValue();
  deleteButton.addEventListener("click", async () => {
    if (idValue === undefined) {
      deleteButton.disabled = true;
      deleteButton.classList.add("disabled");
      return;
    }
    const deleteResponse = await fetch(`/collections/delete/${idValue}/`, {
      method: "DELETE",
      headers: {
        "X-CSRFToken": getCSRFToken()
      }
    });
    if (!deleteResponse.ok) {
      console.error("Failed to delete collection");
    } else {
      window.location.replace("/collections/");
    }
  });
}

// function setupUpdateCollectionSettings() {

// }

function setupResetCollectionSettings() {
  const resetButton = document.getElementById("collection-settings-reset");

  resetButton.addEventListener("click", async (event) => {
    event.preventDefault();
    const collectionId = getCollectionIdValue();
    const resetResponse = await fetch(`/display-collection-settings/${collectionId}/`);
    if (!resetResponse.ok) {
      console.error("Failed to reset collection settings");
      return;
    }
    const settingsHTML = await resetResponse.text();
    const currentSettingsEl = document.getElementById("collection-settings");
    currentSettingsEl.outerHTML = settingsHTML;
    setupCollectionSettings();
  });
}

function setupCollectionSettings() {
  setupDeleteCollection();
  setupResetCollectionSettings();
}

function initialize() {
  setupVideoSearch();
  setupCollectionSettings();
}

initialize();
