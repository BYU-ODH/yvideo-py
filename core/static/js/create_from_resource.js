import { getCSRFToken } from "./utils.js";

function setupOpenModalFunctions() {
  const modal = document.getElementById("create-from-resource-modal");
  const resourceDetails = document.getElementsByClassName("resource-details");
  for (let resourceDetail of resourceDetails) {
    const resourceId = resourceDetail.dataset["resourceId"];
    const collectionId = window.location.pathname.match(/\d.*/g)[0];
    if (resourceId === undefined || collectionId === undefined) {
      console.error("Failed to get create from resource form because of invalid resourceId or collectionId");
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
          collection_id: collectionId
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

function validateForm(collectionIdInput, titleInput, resourceFileInput) {
  let formIsValid = true;
  function markElAsInvalid(el) {
    formIsValid = false;
    el.classList.add("invalid-input");
  }

  if (collectionIdInput?.value === undefined) {
    console.log("Invalid collection_id value!");
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
    const collectionIdInput = document.getElementById("collection-id");
    const titleInput = document.getElementById("content-title-input");
    const resourceFileInput = document.getElementById("resource-file-input");
    if (validateForm(collectionIdInput, titleInput, resourceFileInput)) {
      const createResponse = await fetch("/content/create/", {
        method: "post",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          "collection_id": collectionIdInput.value,
          "title": titleInput.value,
          "resource_file_id": resourceFileInput.value
        })
      });

      if (!createResponse.ok) {
        console.error("Failed to create new content from resource");
        return;
      }

      window.location.replace(`/collections/${collectionIdInput.value}`);
    }
  });
}

function initialize() {
  setupOpenModalFunctions();
}

initialize();
