import { getCSRFToken } from "./utils.js";
import { getPlaylistIdValue } from "./utils.js";

async function displayCreateFromResourceForm(
  resourceDetail,
  playlistId,
  formModal,
  resourcePickerModal,
) {
  const resourceId = resourceDetail.dataset.resourceId;
  if (!resourceId || !playlistId) {
    console.error("Failed to get create from resource form because of invalid resourceId or playlistId");
    return;
  }

  const response = await fetch(
    `/playlists/${playlistId}/create-from-resource/${resourceId}/form/`,
    {
      method: "POST",
      headers: { "X-CSRFToken": getCSRFToken() },
    },
  );
  if (!response.ok) {
    console.error("Failed to get create from resource form because of a system error");
    return;
  }

  const formHTML = await response.text();
  if (!resourcePickerModal.open) {
    return;
  }

  const formContainer = formModal.querySelector("#create-from-resource-form");
  formContainer.outerHTML = formHTML;
  setupCreateResourceForm(formModal);

  resourcePickerModal.close();
  formModal.showModal();
}

function setupResourceDetailHandlers(
  resourceList,
  playlistId,
  formModal,
  resourcePickerModal,
) {
  const resourceDetails = resourceList.querySelectorAll(".resource-details");
  for (const resourceDetail of resourceDetails) {
    resourceDetail.addEventListener("click", () => {
      displayCreateFromResourceForm(
        resourceDetail,
        playlistId,
        formModal,
        resourcePickerModal,
      );
    });
  }
}

function setupPlaylistResourcePicker() {
  const openButton = document.getElementById("add-from-resource-button");
  if (!openButton) {
    return;
  }

  const playlistId = getPlaylistIdValue();
  const addVideoModal = document.getElementById("add-video-dialog");
  const resourcePickerModal = document.getElementById("select-resource-dialog");
  const resourceList = document.getElementById("modal-resource-list");
  const formModal = document.getElementById("create-from-resource-modal");

  openButton.addEventListener("click", async () => {
    const response = await fetch(
      `/playlists/${playlistId}/create-from-resource/resources/`,
    );
    if (!response.ok) {
      console.error("Failed to load resources");
      return;
    }

    const resourceListHTML = await response.text();
    if (!addVideoModal.open) {
      return;
    }

    resourceList.innerHTML = resourceListHTML;
    setupResourceDetailHandlers(
      resourceList,
      playlistId,
      formModal,
      resourcePickerModal,
    );
    addVideoModal.close();
    resourcePickerModal.showModal();
  });
}

function validateForm(playlistIdInput, titleInput, resourceFileInput) {
  let formIsValid = true;
  function markElAsInvalid(el) {
    formIsValid = false;
    el.classList.add("invalid-input");
  }

  if (!playlistIdInput?.value) {
    formIsValid = false;
  }
  if (!titleInput?.value) {
    markElAsInvalid(titleInput);
  }
  if (!resourceFileInput?.value) {
    markElAsInvalid(resourceFileInput);
  }

  return formIsValid;
}

function setupCreateResourceForm(formRoot) {
  const createButton = formRoot.querySelector("#create-from-resource-form-submit");
  createButton.addEventListener("click", async (event) => {
    event.preventDefault();
    const playlistIdInput = formRoot.querySelector("#playlist-id");
    const titleInput = formRoot.querySelector("#content-title-input");
    const resourceFileInput = formRoot.querySelector("#resource-file-input");
    if (!validateForm(playlistIdInput, titleInput, resourceFileInput)) {
      return;
    }

    const response = await fetch(
      `/playlists/${playlistIdInput.value}/content/create/`,
      {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          title: titleInput.value,
          resource_file_id: resourceFileInput.value,
        }),
      },
    );
    if (!response.ok) {
      console.error("Failed to create content from resource");
      return;
    }

    window.location.replace(`/playlists/${playlistIdInput.value}/`);
  });
}

setupPlaylistResourcePicker();
