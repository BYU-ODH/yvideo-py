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
// to a confirmation would take it off whatever they were about to do next.
function announce(message, isError = false) {
  const status = statusElement();
  if (!status) {
    return;
  }
  status.classList.toggle("playlist-members-status-error", isError);
  status.classList.toggle("playlist-members-status-success", !isError);
  status.textContent = message;
}

function clearStatus() {
  const status = statusElement();
  if (status) {
    status.classList.remove(
      "playlist-members-status-error",
      "playlist-members-status-success",
    );
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

const GENERIC_FAILURE = "That didn't work. Reload the page and try again.";

// What the person should be told about a failed response.
//
// Only bodies the endpoints here produce are shown, and those are all text/plain. A 404
// or a 500 is rendered by Django as a full HTML page, and announcing that puts an entire
// error document -- a debug traceback, with DEBUG on -- through a live region. Reachable
// without a bug on our side: a panel left open in one tab while the row it lists is
// removed in another.
async function failureMessage(response) {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.startsWith("text/plain")) {
    return GENERIC_FAILURE;
  }
  const text = (await response.text()).trim();
  return text || GENERIC_FAILURE;
}

// The whole panel, fetched when the dialog opens rather than rendered with the page.
async function loadPanel() {
  const body = document.querySelector(".playlist-members-body");
  if (!body) {
    return;
  }
  try {
    const response = await fetch(`/playlists/${getPlaylistIdValue()}/members/`);
    if (!response.ok) {
      throw new Error(`Manage People responded ${response.status}`);
    }
    body.innerHTML = await response.text();
  } catch (error) {
    console.error(error);
    // Written as text, not markup: whatever went wrong, the response is not to be trusted
    // as HTML, and this is the one place in the panel with nothing else to fall back on.
    body.textContent =
      "Couldn't load the people on this playlist. Close this and try again.";
  }
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
// field is where the next action starts after both adding and removing. If this person
// has no roles to grant there is no search field, so fall back to the dialog itself
// rather than dropping focus to the body and out of the modal.
function focusSearchInput() {
  const searchInput = document.getElementById("playlist-member-search");
  if (searchInput) {
    searchInput.focus();
    return;
  }
  document.getElementById("playlist-members-modal")?.focus();
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
  if (!response.ok) {
    searchInput.classList.add("invalid-input");
    announce(await failureMessage(response), true);
    return;
  }

  const name = option ? option.text : searchInput.value.trim();
  // A provisioned account whose BYU enrollment could not be synced still gets the role,
  // but its course-based access will be wrong until the sync catches up. Said here
  // because the person who granted it is the only one in a position to follow up.
  const warning = response.headers.get("X-Member-Warning");
  resetSearch();
  replaceRoster(await response.text());
  announce(warning ? `${name} added. ${warning}` : `${name} added.`);
  focusSearchInput();
}

// Which change is the current one for each row. Two quick changes on the same select can
// come back out of order, and announcing whichever lands last would tell the person their
// row is something they already changed it away from.
//
// A stale reply is dropped rather than the select being disabled while it is in flight:
// disabling the element someone just used takes focus off it and out to the body.
const latestRoleChange = new Map();

async function changeRole(select) {
  const row = select.closest(".playlist-member-row");
  const userId = row?.dataset.userId;
  if (!userId) {
    return;
  }
  const token = (latestRoleChange.get(userId) ?? 0) + 1;
  latestRoleChange.set(userId, token);

  const body = new FormData();
  body.append("role", select.value);
  const response = await post(`${userId}/role/`, body);
  if (latestRoleChange.get(userId) !== token) {
    return;
  }
  if (!response.ok) {
    console.error("Failed to change playlist member role");
    announce(await failureMessage(response), true);
    return;
  }
  announce((await response.text()).trim());
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
  document.getElementById("playlist-member-remove-modal")?.close();
  if (!response.ok) {
    announce(await failureMessage(response), true);
    return;
  }
  replaceRoster(await response.text());
  announce(`${name} removed.`);
  focusSearchInput();
}

function firstSelectableIndex(options) {
  for (let index = 0; index < options.length; index += 1) {
    if (!options[index].disabled) {
      return index;
    }
  }
  return -1;
}

// ArrowDown moves focus into the results, rather than steering them from the search
// field. A `<select size="4">` is a real listbox: once focus is inside it, the browser
// announces each option as you arrow through. Driving the selection from the input the
// way the spoof picker does announces nothing at all, because focus never moves and there
// is no aria-activedescendant saying otherwise.
//
// Enter still adds from the field itself, so someone typing a NetID never has to visit
// the list to submit it.
function handleSearchKeydown(event) {
  const results = document.getElementById("playlist-member-results");
  if (!results) {
    return;
  }

  if (event.key === "ArrowDown") {
    const index = firstSelectableIndex(results.options);
    if (index < 0) {
      return;
    }
    event.preventDefault();
    results.selectedIndex = index;
    results.focus();
  } else if (event.key === "Enter") {
    event.preventDefault();
    addMember();
  }
}

// Enter adds whoever is highlighted. ArrowUp off the top goes back to the field, so
// correcting a search never needs the mouse.
function handleResultsKeydown(event) {
  if (event.key === "Enter") {
    event.preventDefault();
    addMember();
  } else if (event.key === "ArrowUp" && event.target.selectedIndex <= 0) {
    event.preventDefault();
    document.getElementById("playlist-member-search")?.focus();
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
  } else if (event.target.id === "playlist-member-results") {
    handleResultsKeydown(event);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.classList.contains("playlist-member-role-select")) {
    changeRole(event.target);
  }
});

document.addEventListener("click", (event) => {
  if (event.target.closest("#playlist-manage-people-button")) {
    // The button's own `command`/`commandfor` opens the dialog; this fills it.
    loadPanel();
    return;
  }
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
