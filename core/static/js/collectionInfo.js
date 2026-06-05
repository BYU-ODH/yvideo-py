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
  if (!collectionForm) {
    return;
  }
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
  if (!deleteButton) {
    return;
  }
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

function setupResetCollectionSettings() {
  const resetButton = document.getElementById("collection-settings-reset");
  if (!resetButton) {
    return;
  }
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

function setupSemesterSelectionHandlers() {
  // update the assigned courses displayed whenever the user changes
  // the selected year or semester
  const semesterSelector = document.getElementById("semester-selector");
  const yearSelector = document.getElementById("year-selector");
  const handler = async () => {
    const renderResponse = await fetch("/collections/render-course-assignment/", {
      method: "POST",
      headers: {
        "X-CSRFToken": getCSRFToken(),
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        "semester": semesterSelector.value,
        "year": yearSelector.value,
        "collection_id": getCollectionIdValue()
      })
    });
    if (!renderResponse.ok) {
      console.error("Failed to render assigned courses html");
      return;
    }

    const newHTML = await renderResponse.text();
    const courseAssignmentContainer = document.getElementById("course-assignment-container");
    courseAssignmentContainer.innerHTML = newHTML;
  }

  semesterSelector.addEventListener("change", handler);
  yearSelector.addEventListener("change", handler);
}

function setupCollectionSettings() {
  setupDeleteCollection();
  setupResetCollectionSettings();
  setupSemesterSelectionHandlers();
}

function initialize() {
  setupVideoSearch();
  setupCollectionSettings();
}

initialize();
