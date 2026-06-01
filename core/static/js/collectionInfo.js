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

function setupDeleteCollection() {
  const deleteButton = document.getElementById("collection-confirm-delete");
  const collectionForm = document.getElementById("collection-settings-form");
  deleteButton.addEventListener("click", async () => {
    const idInput = collectionForm.querySelector("input[name='id']");
    const idValue = idInput.value;
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

function initialize() {
  setupVideoSearch();
  setupDeleteCollection();
}

initialize();
