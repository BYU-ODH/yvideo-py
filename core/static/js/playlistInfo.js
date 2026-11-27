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

function setupVideoEntryControls() {
  const videoList = document.getElementById("playlist-video-list");
  if (!videoList) return;

  videoList.addEventListener("click", async (event) => {
    const button = event.target.closest(".copy-video-link-button");
    if (button) {
      const videoUrl = new URL(button.dataset.videoUrl, window.location.origin).href;
      try {
        await navigator.clipboard.writeText(videoUrl);
        displayUserMessage("success", "Video link copied to clipboard.");
      } catch {
        let fail_msg = "Could not copy the video link to your clipboard.";
        if (videoUrl) {
          fail_msg += ` You can copy the link here: ${videoUrl}`;
        }
        displayUserMessage("error", fail_msg);
      }
      return;
    }

    const toggle = event.target.closest(".setting-toggle[data-publish-url]");
    if (!toggle || toggle.disabled) return;
    const published = toggle.getAttribute("aria-checked") !== "true";
    toggle.disabled = true;
    toggle.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(toggle.dataset.publishUrl, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCSRFToken(),
        },
        body: JSON.stringify({ published }),
      });

      if (!response.ok) throw new Error("Publishing update failed")
      toggle.setAttribute("aria-checked", String(published));
      toggle.querySelector(".setting-toggle-text").textContent = published ? "Published" : "Unpublished";
    } catch {
      displayUserMessage("error", "Could not update the video's published status. Please try again.");
    } finally {
      toggle.disabled = false;
      toggle.removeAttribute("aria-busy");
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

  playlistContent.addEventListener("change", async (e) => {
    const settingCheckbox = e.target.closest(".playlist-setting-input");
    if (settingCheckbox) {
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
        document.getElementById(settingCheckbox.id)?.focus();
        displayUserMessage("success", "The playlist settings were updated successfully");
      }
      catch {
        displayUserMessage("warning", "The playlist settings were updated successfully, but the change could not be displayed. Please refresh the page.");
        for (let input of inputs) {
          if (input.isConnected) {
            input.disabled = false;
          }
        }
      }
    }
  });
}

function markInvalid(field) {
  field?.classList.add("invalid-input");
  field?.setAttribute("aria-invalid", "true");
}

// Swapping in a re-rendered form drops focus, so move it to the first problem; screen
// readers then read that field's error, or the form-wide errors.
function focusFirstError(form, errorsSelector) {
  const target = form.querySelector("[aria-invalid='true']") ?? form.querySelector(errorsSelector);
  target?.focus();
}

function clearInvalid(event) {
  event.target.classList.remove("invalid-input");
  event.target.removeAttribute("aria-invalid");
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

  for (const dialog of [addDialog, editDialog]) {
    dialog.addEventListener("invalid", (event) => markInvalid(event.target), true);
    dialog.addEventListener("input", clearInvalid);
    dialog.addEventListener("change", clearInvalid);

    dialog.addEventListener("submit", async (event) => {
      const form = event.target;
      if (!form.matches("[data-course-assignment-form]")) return;
      event.preventDefault();
      const submitButton = form.querySelector("button[type='submit']");
      if (submitButton.disabled) return;

      if (form.id === "assign-courses-form") {
        form.elements.namedItem("yearterm").value = semesterSelector.value;
      }

      submitButton.disabled = true;

      try {
        const response = await fetch(form.action, {
          method: form.method,
          body: new FormData(form)
        });

        if (response.status === 400) {
          const template = document.createElement("template");
          template.innerHTML = await response.text();
          const updatedForm = template.content.querySelector("form[data-course-assignment-form]");
          if (!updatedForm) throw new Error("No assignment form returned");
          form.replaceWith(updatedForm);
          const invalidFields = updatedForm.querySelectorAll("[aria-invalid='true']");
          invalidFields.forEach(markInvalid);
          focusFirstError(updatedForm, "[data-course-form-errors]");
          return;
        }
        if (!response.ok) throw new Error("Assignment failed");
        window.location.reload();
      } catch {
        dialog.close();
        submitButton.disabled = false;
        displayUserMessage("error", "An error occurred while attempting to assign courses to the playlist. Please refresh the page to continue.");
      }
    });
  }
}

function setupUnassignCourses() {
  const dialog = document.getElementById("delete-assigned-course-modal");
  const courseList = document.getElementById("assigned-courses");
  const semesterSelector = document.getElementById("playlist-semester-selector");
  if (!dialog || !courseList || !semesterSelector) return;

  const confirmButton = dialog.querySelector("#unassign-course-confirm");
  const description = dialog.querySelector("#unassign-course-description");
  let courseToUnassign;
  let requestPending = false;

  courseList.addEventListener("click", (event) => {
    const button = event.target.closest(".assigned-course-delete");
    if (!button) return;
    const courseLine = button.closest(".semester-course");
    courseToUnassign = {
      dept: courseLine.dataset.dept,
      catalog_number: courseLine.dataset.catalogNumber,
      yearterm: semesterSelector.value
    };
    const semester = semesterSelector.selectedOptions[0].textContent.trim();
    description.textContent = `Remove ${courseToUnassign.dept} ${courseToUnassign.catalog_number}, and its sections from this playlist for ${semester}?`;
    confirmButton.disabled = requestPending;
  });

  dialog.addEventListener("close", () => {
    courseToUnassign = undefined;
    confirmButton.disabled = true;
  });

  confirmButton.addEventListener("click", async () => {
    if (!courseToUnassign || requestPending) return;
    const target = courseToUnassign;
    requestPending = true;
    confirmButton.disabled = true;

    try {
      await fetch(confirmButton.dataset.unassignUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify(target)
      });
    } catch {
      displayUserMessage("error", "The course could not be removed from the playlist. Please try again.");
      requestPending = false;
      confirmButton.disabled = !courseToUnassign;
      return;
    }
    // The server queues its success or error message for the reloaded page to show.
    window.location.reload();
  });
}

function setupAssignMember(dialogId) {
  const dialog = document.getElementById(dialogId);
  if (!dialog) return;
  let assignmentPending = false;

  dialog.addEventListener("invalid", (event) => markInvalid(event.target), true);
  dialog.addEventListener("input", clearInvalid);
  dialog.addEventListener("change", clearInvalid);

  // PR EXPLANATION: the search results are submit buttons, so Enter in the search field would
  // "click" the first result and assign someone the user never picked.
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.name === "search") event.preventDefault();
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;

    // Arrow keys step between the search field and its results, like Tab and Shift+Tab.
    const form = event.target.closest("form[data-individual-assignment-form]");
    if (!form) return;
    const items = [
      form.elements.namedItem("search"),
      ...form.querySelectorAll("button[data-individual-user-id]:not(:disabled)"),
    ];
    const index = items.indexOf(event.target);
    if (index === -1) return;
    event.preventDefault();
    items[index + (event.key === "ArrowDown" ? 1 : -1)]?.focus();
  });

  dialog.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!form.matches("[data-individual-assignment-form]")) return;
    event.preventDefault();

    const submitButton = event.submitter;
    if (!submitButton?.matches("button[data-individual-user-id]") || assignmentPending) return;
    form.elements.namedItem("user_id").value = submitButton.dataset.individualUserId;
    const search = form.elements.namedItem("search");
    search.classList.remove("invalid-input");
    search.removeAttribute("aria-invalid");
    const resultButtons = form.querySelectorAll("button[data-individual-user-id]");
    const errors = form.querySelector("[data-assignment-form-errors]");

    assignmentPending = true;
    resultButtons.forEach((button) => { button.disabled = true; });
    search.readOnly = true;
    errors.hidden = true;
    clearTimeout(searchTimeout);
    ++requestId;

    try {
      const response = await fetch(form.action, {
        method: form.method,
        body: new FormData(form),
      });

      if (response.status === 400) {
        const template = document.createElement("template");
        template.innerHTML = await response.text();

        const updatedForm = template.content.querySelector("form[data-individual-assignment-form]");
        if (!updatedForm || updatedForm.id !== form.id) throw new Error("No matching assignment form returned");

        form.replaceWith(updatedForm);
        const invalidFields = updatedForm.querySelectorAll("[aria-invalid='true']");
        invalidFields.forEach(markInvalid);
        focusFirstError(updatedForm, "[data-assignment-form-errors]");
        return;
      }
      if (response.status !== 200) throw new Error("Assignment failed");
      window.location.reload();
    } catch {
      displayUserMessage("error", "Could not assign this person to the playlist. Please try again.")
      resultButtons.forEach((button) => { button.disabled = false; });
      search.readOnly = false;
    } finally {
      assignmentPending = false;
    }
  });

  let searchTimeout;
  let requestId = 0;

  dialog.addEventListener("close", () => {
    clearTimeout(searchTimeout);
    ++requestId;
  });

  dialog.addEventListener("input", (event) => {
    if (event.target.name !== "search") return;
    const form = event.target.form;
    const results = form.querySelector("[data-individual-search-results]");
    const errors = form.querySelector("[data-assignment-form-errors]");
    const query = event.target.value.trim();
    const currentRequest = ++requestId;
    clearTimeout(searchTimeout);
    form.elements.namedItem("user_id").value = "";

    results.replaceChildren();
    errors.hidden = true;
    searchTimeout = setTimeout(async () => {
      const body = new FormData();
      body.append("search", query);
      try {
        const response = await fetch(form.dataset.searchUrl, {
          method: "POST",
          headers: { "X-CSRFToken": getCSRFToken() },
          body,
        });
        if (!response.ok || response.redirected) throw new Error("Search failed");
        const html = await response.text();
        if (currentRequest !== requestId || !results.isConnected) return;
        results.innerHTML = html;
      } catch {
        if (currentRequest !== requestId || !results.isConnected) return;
        displayUserMessage("error", "An error occurred while searching for a matching user. Please clear the search text and try again")
      }
    }, 300);
  });

}


function setupRemoveMember() {
  const dialog = document.getElementById("delete-assigned-individual-modal");
  const pncContent = document.getElementById("playlist-publishing-and-collaborators-content");
  if (!dialog || !pncContent) return;
  const confirmButton = dialog.querySelector("#unassign-individual-confirm");
  const description = dialog.querySelector("#unassign-individual-description");
  let removeUrl;
  let requestPending = false;

  pncContent.addEventListener("click", (event) => {
    const button = event.target.closest(".assigned-individual-delete");
    if (!button || button.disabled || requestPending) return;
    removeUrl = button.dataset.removeUrl;
    description.textContent = `Remove ${button.dataset.individualName}'s direct access to this playlist?`;
    confirmButton.disabled = false;
  });

  dialog.addEventListener("close", () => {
    removeUrl = undefined;
    confirmButton.disabled = true;
  });

  confirmButton.addEventListener("click", async () => {
    if (!removeUrl || requestPending) return;
    const target = removeUrl;
    requestPending = true;
    confirmButton.disabled = true;
    try {
      const response = await fetch(target, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
      });
      if (!response.ok || response.redirected) throw new Error("Could not remove membership");
      window.location.reload();
    } catch {
      displayUserMessage("error", "The user's access could not be removed. Please try again.");
      requestPending = false;
      confirmButton.disabled = !removeUrl;
    }
  });
}


function initialize() {
  setupVideoSearch();
  setupVideoEntryControls();
  setupDeletePlaylist();
  setupPlaylistSettings();
  setupAssignCourses();
  setupAssignMember("add-assigned-individual-modal");
  setupAssignMember("add-assigned-collaborator-modal");
  setupRemoveMember();

  setupUnassignCourses();
  const playlistNameWrapper = document.getElementById("playlist-name-wrapper");
  const playlistId = getPlaylistIdValue();
  if (playlistNameWrapper?.querySelector("button") && playlistId !== undefined) {
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
