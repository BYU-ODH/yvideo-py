import { getCSRFToken } from "./utils.js";

function setupCollectionSearch() {
  const collectionSearch = document.getElementById("collections-search");
  collectionSearch.addEventListener("input", () => {
    const searchText = collectionSearch.value.toLowerCase();
    const landingPageList = collectionSearch.closest(".landing-page-collection-list");
    const collections = landingPageList.querySelectorAll(".landing-page-collection");
    for (let collection of collections) {
      const name = collection.querySelector(".collection-header-name")?.innerText.toLowerCase() || "";
      collection.style.display = name.includes(searchText) ? "" : "none";
    }
  });
}

function setupNewCollectionSubmit() {
  const form = document.getElementById("new-collection-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const nameInput = form.querySelector("#new-collection-name-input");
    const response = await fetch("/collections/create/", {
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
      console.error("Failed to save new collection");
      return;
    }
    location.reload();
  });
}

function initialize() {
  setupCollectionSearch();
  setupNewCollectionSubmit();
}

initialize();
