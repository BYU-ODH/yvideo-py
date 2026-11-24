import { getCSRFToken, displayUserMessage, setupInPlaceFieldEdit } from "./utils.js";
import { getPlaylistIdValue } from "./utils.js";

function setupVideoSearch() {
  const searchInput = document.getElementById("video-search");
  const videoList = document.getElementById("playlist-video-list");
  searchInput.addEventListener("input", () => {
    const searchText = searchInput.value.toLowerCase();
    const videos = videoList.querySelectorAll(".playlist-item-tile");
    for (const video of videos) {
      const title = video.querySelector(".video-item-title")?.innerText.toLowerCase() || "";
      video.style.display = title.includes(searchText) ? "" : "none";
    }
  });
}

function setupDeletePlaylist() {
  const deleteButton = document.getElementById("playlist-confirm-delete");
  if (!deleteButton) {
    return;
  }
  const idValue = getPlaylistIdValue();
  deleteButton.addEventListener("click", async () => {
    if (idValue === undefined) {
      deleteButton.disabled = true;
      deleteButton.classList.add("disabled");
      return;
    }
    const deleteResponse = await fetch(`/playlists/${idValue}/delete/`, {
      method: "DELETE",
      headers: {
        "X-CSRFToken": getCSRFToken()
      }
    });
    if (!deleteResponse.ok) {
      // A plain-text body is a message written for the user (e.g. "scheduled in an IC
      // program"); anything else is an error page and gets the generic message.
      const isMessage = (deleteResponse.headers.get("content-type") ?? "").startsWith("text/plain");
      displayUserMessage("error", isMessage ? (await deleteResponse.text()) : "Failed to delete playlist");
    } else {
      window.location.replace("/playlists/");
    }
  });
}

function setupPlaylistSettings() {
  const playlistContent = document.getElementById("playlist-content");
  if (!playlistContent) return;

  // stop form from refreshing page. We want to replace the form HTML
  // on each input change.
  playlistContent.addEventListener("submit", (e) => {
    e.preventDefault();
  });

  playlistContent.addEventListener("click", async (e) => {
    const checkboxButton = e.target.closest(".playlist-setting-checkbox-button");
    if (checkboxButton) {
      const settingCheckbox = checkboxButton.querySelector("input[type='checkbox']");
      if (!settingCheckbox) return;
      settingCheckbox.checked = !settingCheckbox.checked;

      const playlistSettings = document.getElementById("playlist-settings");
      if (!playlistSettings) return;

      const settingsData = new FormData(playlistSettings);

      // We don't want the user to make another change while request is in flight
      const inputs = playlistSettings.querySelectorAll(".playlist-setting-input");
      for (let input of inputs) {
        input.disabled = true;
      }

      const result = await fetch(playlistSettings.action, {
        method: playlistSettings.method,
        body: settingsData
      });
      if (!result.ok) {
        displayUserMessage("error", "The playlist settings could not be updated. Please refresh the page.");
        return;
      }

      try {
        const updatedForm = await result.text();
        playlistSettings.outerHTML = updatedForm;
        displayUserMessage("success", "The playlist settings were updated successfully");
      }
      catch {
        displayUserMessage("warning", "The playlist settings were updated successfully, but the change could not be displayed. Please refresh the page.");
      }
      for (let input of inputs) {
        if (input.isConnected) {
          input.disabled = true;
        }
      }
    }
  });
}

function setupAssignCourses() {
  const addDialog = document.getElementById("add-assigned-course-modal");
  const editDialog = document.getElementById("edit-assigned-course");
  const semesterSelector = document.getElementById("playlist-semester-selector");
  if (!addDialog || !editDialog || !semesterSelector) return;

  semesterSelector.addEventListener("change", () => {
    const url = new URL(window.location.href);
    url.searchParams.set("yearterm", semesterSelector.value);
    window.location.assign(url.href);
  });

  let editRequestId = 0;
  document.getElementById("assigned-courses").addEventListener("click", async (event) => {
    const button = event.target.closest(".edit-course-button");
    if (!button) return;
    const requestId = ++editRequestId;
    const courseLine = button.closest(".semester-course");
    const container = editDialog.querySelector("#edit-courses-form-container");
    container.textContent = "Loading course assignment…";

    const url = new URL(button.dataset.editUrl, window.location.origin);
    url.searchParams.set("dept", courseLine.dataset.dept);
    url.searchParams.set("catalog_number", courseLine.dataset.catalogNumber);
    url.searchParams.set("yearterm", semesterSelector.value);
    try {
      const response = await fetch(url);
      if (!response.ok || response.redirected) {
        throw new Error("Could not load course assignment.");
      }
      const html = await response.text();
      if (requestId !== editRequestId || !editDialog.open) return;
      container.innerHTML = html;
    } catch {
      if (requestId === editRequestId && editDialog.open) {
        container.textContent = "Could not load course assignment. Close this dialog and try again.";
      }
    }
  });

  const failureMessage = "Could not save course assignments. Please try again.";

  function markInvalid(field) {
    field?.classList.add("invalid-input");
    field?.setAttribute("aria-invalid", "true");
  }

  function clearInvalid(event) {
    event.target.classList.remove("invalid-input");
    event.target.removeAttribute("aria-invalid");
  }

  for (const dialog of [addDialog, editDialog]) {
    dialog.addEventListener("invalid", (event) => markInvalid(event.target), true);
    dialog.addEventListener("input", clearInvalid);
    dialog.addEventListener("change", clearInvalid);

    dialog.addEventListener("submit", async (event) => {
      const form = event.target;
      if (!form.matches("[data-course-assignment-form]")) return;
      event.preventDefault();
      const submitButton = form.querySelector("button[type='submit']");
      const errors = form.querySelector("[data-course-form-errors]");
      if (submitButton.disabled) return;

      if (form.id === "assign-courses-form") {
        form.elements.namedItem("yearterm").value = semesterSelector.value;
      }
      errors.hidden = true;
      submitButton.disabled = true;
      try {
        const response = await fetch(form.action, {
          method: form.method,
          body: new FormData(form)
        });
        if (response.status === 400) {
          form.outerHTML = await response.text();
          const updatedForm = dialog.querySelector("[data-course-assignment-form]");
          const invalidFields = updatedForm.querySelectorAll("[aria-invalid='true']");
          invalidFields.forEach(markInvalid);
          return;
        }
        if (!response.ok || response.redirected) {
          displayUserMessage("error", failureMessage);
          return;
        }
        window.location.reload();
      } catch {
        errors.textContent = failureMessage;
        errors.hidden = false;
        errors.focus();
      } finally {
        submitButton.disabled = false;
      }
    });
  }
}

function initialize() {
  setupVideoSearch();
  setupDeletePlaylist();
  setupPlaylistSettings();
  setupAssignCourses();
  const playlistNameWrapper = document.getElementById("playlist-name-wrapper");
  const playlistId = getPlaylistIdValue();
  if (playlistNameWrapper && playlistId !== undefined) {
    setupInPlaceFieldEdit(playlistNameWrapper, `/playlists/${playlistId}/change-name/`, {
      onSuccess: () => {
        const playlistTitle = playlistNameWrapper.querySelector("h3");
        const playlistInput = playlistNameWrapper.querySelector("input");
        if (!playlistTitle || !playlistInput) {
          displayUserMessage("error", "Failed to display updated playlist name. Please refresh the page.");
          return;
        }
        playlistTitle.textContent = playlistInput.value;
      }
    });
  }
}

initialize();
