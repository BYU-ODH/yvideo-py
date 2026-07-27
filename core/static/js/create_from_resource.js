import { getCSRFToken } from "./utils.js";

function setupOpenModalFunctions() {
  const modal = document.getElementById("create-from-resource-modal");
  const resourceDetails = document.getElementsByClassName("resource-details");
  for (let resourceDetail of resourceDetails) {
    const resourceId = resourceDetail.dataset["resourceId"];
    const playlistId = window.location.pathname.match(/\d.*/g)[0];
    if (resourceId === undefined || playlistId === undefined) {
      console.error("Failed to get create from resource form because of invalid resourceId or playlistId");
      return;
    }
    resourceDetail.addEventListener("click", async () => {
      const newFormResponse = await fetch("/create-from-resource-form/", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          resource_id: resourceId,
          playlist_id: playlistId
        })
      });
      if (!newFormResponse.ok) {
        console.error("Failed to get create from resource form because of a system error");
        return;
      }
      const newFormHTML = await newFormResponse.text();
      const formEl = document.getElementById("create-from-resource-form");
      formEl.outerHTML = newFormHTML;
      setupCreateResourceForm();
      modal.showModal();
    });
  }
}

function validateForm(playlistIdInput, titleInput, resourceFileInput) {
  let formIsValid = true;
  function markElAsInvalid(el) {
    formIsValid = false;
    el.classList.add("invalid-input");
  }

  if (playlistIdInput?.value === undefined) {
    console.log("Invalid playlist_id value!");
    formIsValid = false;
  }
  if (titleInput.value === undefined) {
    markElAsInvalid(titleInput);
  }
  if (resourceFileInput === undefined || resourceFileInput === '') {
    markElAsInvalid(resourceFileInput);
  }

  return formIsValid;
}

function setupCreateResourceForm() {
  const createButton = document.getElementById("create-from-resource-form-submit");
  createButton.addEventListener("click", async (event) => {
    event.preventDefault();
    const playlistIdInput = document.getElementById("playlist-id");
    const titleInput = document.getElementById("content-title-input");
    const resourceFileInput = document.getElementById("resource-file-input");
    if (validateForm(playlistIdInput, titleInput, resourceFileInput)) {
      const createResponse = await fetch("/content/create/", {
        method: "post",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          "playlist_id": playlistIdInput.value,
          "title": titleInput.value,
          "resource_file_id": resourceFileInput.value
        })
      });

      if (!createResponse.ok) {
        console.error("Failed to create new content from resource");
        return;
      }

      window.location.replace(`/playlists/${playlistIdInput.value}`);
    }
  });
}

function initialize() {
  setupOpenModalFunctions();
}

initialize();
