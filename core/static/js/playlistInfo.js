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

function markInvalid(field) {
  field?.classList.add("invalid-input");
  field?.setAttribute("aria-invalid", "true");
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
  const errors = dialog.querySelector("#unassign-course-error");
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
    const sections = courseLine.querySelector(".course-sections").textContent.trim();
    const semester = semesterSelector.selectedOptions[0].textContent.trim();
    description.textContent = `Remove ${courseToUnassign.dept} ${courseToUnassign.catalog_number}, and its sections from this playlist for ${semester}?`;
    errors.hidden = true;
    confirmButton.disabled = requestPending;
  });

  dialog.addEventListener("close", () => {
    courseToUnassign = undefined;
    confirmButton.disabled = true;
    errors.hidden = true;
  });

  confirmButton.addEventListener("click", async () => {
    if (!courseToUnassign || requestPending) return;
    const target = courseToUnassign;
    requestPending = true;
    confirmButton.disabled = true;
    errors.hidden = true;

    const response = await fetch(confirmButton.dataset.unassignUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCSRFToken(),
        "Content-Type": "application/json"
      },
      body: JSON.stringify(target)
    });
    window.location.reload();
  });
}

function setupAssignIndividual() {
  const dialog = document.getElementById("add-assigned-individual-modal");
  if (!dialog) return;

  dialog.addEventListener("invalid", (event) => markInvalid(event.target), true);
  dialog.addEventListener("input", clearInvalid);
  dialog.addEventListener("change", clearInvalid);

  dialog.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!form.matches("[data-individual-assignment-form]")) return;
    event.preventDefault();

    const submitButton = form.querySelector("button[type='submit']");
    if (submitButton.disabled) return;
    if (!form.elements.namedItem("user_id").value) {
      markInvalid(form.elements.namedItem("search"));
      return;
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
        const updatedForm = template.content.querySelector("form[data-individual-assignment-form]");
        if (!updatedForm) throw new Error("No assignment form returned");
        form.replaceWith(updatedForm);
        const invalidFields = updatedForm.querySelectorAll("[aria-invalid='true']");
        invalidFields.forEach(markInvalid);
        return;
      }
      if (!response.ok) throw new Error("Assignment failed");
      window.location.reload();
    } catch {
      dialog.close();
      submitButton.disabled = false;
      displayUserMessage("error", "An error occurred while atempting to assign the user to the playlist. Please refresh the page to continue.");
    }
  });

  let searchTimeout;
  let requestId = 0;

  dialog.addEventListener("input", (event) => {
    if (event.target.name !== "search") return;
    const form = event.target.form;
    const results = form.querySelector("[data-individual-search-results]");
    const errors = form.querySelector("[data-assignment-form-errors]");
    const query = event.target.value.trim();
    const currentRequest = ++requestId;
    clearTimeout(searchTimeout);
    form.elements.namedItem("user_id").value = "";
    form.querySelector("[data-selected-person]").textContent = "No person selected.";
    results.replaceChildren();
    errors.hidden = true;
    searchTimeout = setTimeout(async () => {
      const body = new FormData();
      body.append("search", query);
      try {
        const response = await fetch(form.dataset.searchUrl, {
          method: "POST",
          headers: { "X-CSRFToken": getCSRFToken() },
          body
        });
        if (!response.ok || response.redirected) throw new Error("Search failed");
        const html = await response.text();
        if (currentRequest !== requestId || !results.isConnected) return;
        results.innerHTML = html;
      } catch {
        if (currentRequest !== requestId || !results.isConnected) return;
        errors.textContent = "Could not search for people. Please try again.";
        errors.hidden = false;
      }
    }, 300);
  });

  dialog.addEventListener("click", (event) => {
    const button = event.target.closest("[data-individual-user-id]");
    if (!button) return;
    const form = button.form;
    form.elements.namedItem("user_id").value = button.dataset.individualUserId;
    form.querySelector("[data-selected-person]").textContent = `Selected: ${button.textContent.trim()}`;
    form.querySelectorAll("[data-individual-user-id]").forEach((result) => {
      result.setAttribute("aria-pressed", String(result === button));
    });
    const search = form.elements.namedItem("search");
    search.classList.remove("invalid-input");
    search.removeAttribute("aria-invalid");
    form.querySelector("[data-assignment-form-errors]").hidden = true;
  });
}

function setupEditIndividual() {
  const dialog = document.getElementById("edit-assigned-individual-modal");
  const individualList = document.getElementById("assigned-individuals-list");
  if (!dialog || !individualList) return;
  let requestId = 0;

  dialog.addEventListener("invalid", (event) => markInvalid(event.target), true);
  dialog.addEventListener("input", clearInvalid);
  dialog.addEventListener("change", clearInvalid);

  individualList.addEventListener("click", async (event) => {
    const button = event.target.closest(".assigned-individual-edit");
    if (!button) return;
    const currentRequest = ++requestId;
    const container = dialog.querySelector("#edit-individual-form-container");
    container.textContent = "Loading individual assignment…";
    try {
      const response = await fetch(button.dataset.editUrl);
      if (!response.ok || response.redirected) throw new Error("Could not load assignment");
      const html = await response.text();
      if (currentRequest !== requestId || !dialog.open) return;
      container.innerHTML = html;
      if (!container.querySelector("form[data-edit-individual-form]")) {
        throw new Error("No assignment form returned");
      }
    } catch {
      if (currentRequest !== requestId || !dialog.open) return;
      dialog.close();
      displayUserMessage("error", "An error occurred while loading the individual's role. Please refresh the page to continue.");
    }
  });

  dialog.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!form.matches("[data-edit-individual-form]")) return;
    event.preventDefault();
    const submitButton = form.querySelector("button[type='submit']");
    if (submitButton.disabled) return;
    submitButton.disabled = true;
    try {
      const response = await fetch(form.action, {
        method: form.method,
        body: new FormData(form)
      });
      if (response.redirected) throw new Error("Unexpected redirect");
      if (response.status === 400) {
        const template = document.createElement("template");
        template.innerHTML = await response.text();
        const updatedForm = template.content.querySelector("form[data-edit-individual-form]");
        if (!updatedForm) throw new Error("No assignment form returned");
        form.replaceWith(updatedForm);
        updatedForm.querySelectorAll("[aria-invalid='true']").forEach(markInvalid);
        return;
      }
      if (!response.ok) throw new Error("Could not update role");
      window.location.reload();
    } catch {
      dialog.close();
      displayUserMessage("error", "An error occurred while updating the individual's role. Please refresh the page to continue.");
    }
  });
}

function setupUnassignIndividual() {
  const dialog = document.getElementById("delete-assigned-individual-modal");
  const individualList = document.getElementById("assigned-individuals-list");
  if (!dialog || !individualList) return;
  const confirmButton = dialog.querySelector("#unassign-individual-confirm");
  const description = dialog.querySelector("#unassign-individual-description");
  let removeUrl;
  let requestPending = false;

  individualList.addEventListener("click", (event) => {
    const button = event.target.closest(".assigned-individual-delete");
    if (!button) return;
    removeUrl = button.dataset.removeUrl;
    const yearterm = document.getElementById("playlist-semester-selector").selectedOptions[0].textContent.trim();
    description.textContent = `Remove ${button.dataset.individualName}'s access to this playlist from ${yearterm}?`;
    confirmButton.disabled = requestPending;
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
        headers: { "X-CSRFToken": getCSRFToken() }
      });
      if (!response.ok || response.redirected) throw new Error("Could not remove individual");
      window.location.reload();
    } catch {
      requestPending = false;
      dialog.close();
      displayUserMessage("error", "An error occurred while removing the individual from the playlist. Please refresh the page to continue.");
    }
  });
}


function initialize() {
  setupVideoSearch();
  setupDeletePlaylist();
  setupPlaylistSettings();
  setupAssignCourses();
  setupAssignIndividual();
  setupEditIndividual();
  setupUnassignIndividual();

  setupUnassignCourses();
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
