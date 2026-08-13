// Manage People, the playlist role editor. Every handler is delegated from `document`
// rather than bound to elements, because two different swaps replace this markup: the
// settings Reset replaces the whole panel, and add/remove replace the member list.
// Rebinding after each would mean remembering to, and forgetting is silent.
import { getCSRFToken } from "./utils.js";
import { getPlaylistIdValue } from "./utils.js";

const SEARCH_DEBOUNCE_MS = 300;

function statusElement() {
  return document.getElementById("playlist-members-status");
}

// Announced rather than focused: the person is mid-task in the dialog, and moving focus
// to a confirmation would take it off whatever they were about to do next. Revealing the
// element is what announces it, which is why it ships hidden.
function announce(message, isError = false) {
  const status = statusElement();
  if (!status) {
    return;
  }
  status.textContent = message;
  status.classList.toggle("playlist-members-status-error", isError);
  status.classList.toggle("playlist-members-status-success", !isError);
  status.hidden = false;
}

function clearStatus() {
  const status = statusElement();
  if (status) {
    status.hidden = true;
    status.textContent = "";
  }
}

async function post(path, body) {
  const response = await fetch(`/playlists/${getPlaylistIdValue()}/members/${path}`, {
    method: "POST",
    headers: { "X-CSRFToken": getCSRFToken() },
    body: body,
  });
  return response;
}

// Only the rows are replaced. The status message below them, and the add controls below
// that, are left alone -- see render_playlist_members_roster for why.
function replaceRoster(html) {
  const roster = document.getElementById("playlist-members-roster");
  if (roster) {
    roster.innerHTML = html;
  }
}

function resetSearch() {
  const searchInput = document.getElementById("playlist-member-search");
  const results = document.getElementById("playlist-member-results");
  if (searchInput) {
    searchInput.value = "";
    searchInput.classList.remove("invalid-input");
  }
  if (results) {
    results.innerHTML = "";
    results.classList.add("hidden");
  }
}

// The list is replaced wholesale, so focus has to be put back deliberately. The search
// field is where the next action starts after both adding and removing.
function focusSearchInput() {
  document.getElementById("playlist-member-search")?.focus();
}

function selectedResultOption() {
  const results = document.getElementById("playlist-member-results");
  const option = results?.options[results.selectedIndex];
  return option && option.value && !option.disabled ? option : null;
}

async function runSearch(query) {
  const results = document.getElementById("playlist-member-results");
  if (!results) {
    return;
  }
  const body = new FormData();
  body.append("search", query);
  const response = await post("search/", body);
  if (!response.ok) {
    console.error("Failed to search for playlist members");
    return;
  }
  results.innerHTML = await response.text();
  const selectable = Array.from(results.options).some((option) => !option.disabled);
  results.classList.toggle("hidden", !selectable);
}

let searchTimeout = null;

function handleSearchInput(event) {
  // Editing the field is the person acting on the last failure, so stop marking it as
  // one. The message itself stays until something replaces it.
  event.target.classList.remove("invalid-input");
  window.clearTimeout(searchTimeout);
  const query = event.target.value.trim();
  searchTimeout = window.setTimeout(() => runSearch(query), SEARCH_DEBOUNCE_MS);
}

async function addMember() {
  const searchInput = document.getElementById("playlist-member-search");
  const roleSelect = document.getElementById("playlist-member-role");
  if (!searchInput || !roleSelect) {
    return;
  }

  const body = new FormData();
  body.append("role", roleSelect.value);
  const option = selectedResultOption();
  if (option) {
    body.append("user_id", option.value);
  } else {
    // Nothing picked from the results, so treat the typed text as a NetID or BYU ID and
    // let the server's directory lookup decide whether it names a real person.
    body.append("identifier", searchInput.value.trim());
  }

  clearStatus();
  const response = await post("add/", body);
  const text = await response.text();
  if (!response.ok) {
    searchInput.classList.add("invalid-input");
    announce(text, true);
    return;
  }

  const name = option ? option.text : searchInput.value.trim();
  resetSearch();
  replaceRoster(text);
  announce(`${name} added.`);
  focusSearchInput();
}

async function changeRole(select) {
  const row = select.closest(".playlist-member-row");
  const userId = row?.dataset.userId;
  if (!userId) {
    return;
  }
  const body = new FormData();
  body.append("role", select.value);
  const response = await post(`${userId}/role/`, body);
  const text = await response.text();
  if (!response.ok) {
    console.error("Failed to change playlist member role");
    announce(text, true);
    return;
  }
  announce(text);
}

function openRemoveConfirmation(button) {
  const row = button.closest(".playlist-member-row");
  const dialog = document.getElementById("playlist-member-remove-modal");
  const confirmButton = document.getElementById("playlist-member-confirm-remove");
  const nameElement = document.getElementById("playlist-member-remove-name");
  if (!row || !dialog || !confirmButton || !nameElement) {
    return;
  }
  const name = row.querySelector(".playlist-member-name")?.textContent.trim() ?? "";
  nameElement.textContent = `${name} will lose the access this playlist gives them.`;
  confirmButton.dataset.userId = row.dataset.userId;
  confirmButton.dataset.userName = name;
  dialog.showModal();
}

async function removeMember(confirmButton) {
  const userId = confirmButton.dataset.userId;
  const name = confirmButton.dataset.userName ?? "";
  if (!userId) {
    return;
  }
  const response = await post(`${userId}/remove/`, new FormData());
  const text = await response.text();
  document.getElementById("playlist-member-remove-modal")?.close();
  if (!response.ok) {
    announce(text, true);
    return;
  }
  replaceRoster(text);
  announce(`${name} removed.`);
  focusSearchInput();
}

// Arrow keys move through the results without leaving the search field, matching the
// spoof user picker. Enter adds whoever is highlighted.
function handleSearchKeydown(event) {
  const results = document.getElementById("playlist-member-results");
  if (!results) {
    return;
  }
  const options = results.options;
  const selectable = options.length > 0 && !(options.length === 1 && options[0].disabled);

  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    if (!selectable) {
      return;
    }
    event.preventDefault();
    const direction = event.key === "ArrowDown" ? 1 : -1;
    let index = results.selectedIndex;
    if (index < 0) {
      index = direction === 1 ? 0 : options.length - 1;
    } else {
      index = Math.min(Math.max(index + direction, 0), options.length - 1);
    }
    results.selectedIndex = index;
    options[index].scrollIntoView({ block: "nearest" });
  } else if (event.key === "Enter") {
    event.preventDefault();
    addMember();
  }
}

document.addEventListener("input", (event) => {
  if (event.target.id === "playlist-member-search") {
    handleSearchInput(event);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.target.id === "playlist-member-search") {
    handleSearchKeydown(event);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.classList.contains("playlist-member-role-select")) {
    changeRole(event.target);
  }
});

document.addEventListener("click", (event) => {
  if (event.target.closest("#playlist-member-add-button")) {
    addMember();
    return;
  }
  const removeButton = event.target.closest(".playlist-member-remove-button");
  if (removeButton) {
    openRemoveConfirmation(removeButton);
    return;
  }
  if (event.target.closest("#playlist-member-confirm-remove")) {
    removeMember(document.getElementById("playlist-member-confirm-remove"));
  }
});
