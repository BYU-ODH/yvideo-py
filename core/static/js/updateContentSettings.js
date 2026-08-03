import { getCSRFToken } from "./utils.js";

function setupSubmit() {
  const submitButton = document.getElementById("content-settings-submit-button");
  submitButton.addEventListener("click", async () => {
    const idInput = document.getElementById("content-id-input");
    const titleInput = document.getElementById("title");
    const publishedInput = document.getElementById("published");
    const allowDefsInput = document.getElementById("allow-definitions");
    const allowNotesInput = document.getElementById("allow-notes");
    const allowCaptsInput = document.getElementById("allow-captions");
    const allowFastPlaybackInput = document.getElementById("allow-fast-playback");
    const clipsOnlyInput = document.getElementById("clips-only");
    const wordsInput = document.getElementById("words");
    const descriptionInput = document.getElementById("description");
    const isUndefined = [idInput, titleInput, publishedInput, allowDefsInput, allowNotesInput, allowCaptsInput, allowFastPlaybackInput, clipsOnlyInput, wordsInput, descriptionInput].some(el => el === undefined);
    if (isUndefined) {
      console.log("at least one content settings form input is undefined.");
      return;
    }
    await fetch("/content/update/", {
      method: "POST",
      headers: {
        "X-CSRFToken": getCSRFToken(),
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        "id": idInput.value,
        "title": titleInput.value,
        "published": publishedInput.checked,
        "allow_definitions": allowDefsInput.checked,
        "allow_notes": allowNotesInput.checked,
        "allow_captions": allowCaptsInput.checked,
        "allow_fast_playback": allowFastPlaybackInput.checked,
        "clips_only": clipsOnlyInput.checked,
        "words": wordsInput.value,
        "description": descriptionInput.value,
      })
    });
    window.location.reload();
  });
}

function setupReset() {
  const resetButton = document.getElementById("content-settings-reset-button");
  const contentIdInput = document.getElementById("content-id-input");
  if (!resetButton || !contentIdInput) {
    console.error("resetButton or contentIdInput are not defined");
    return;
  }
  resetButton.addEventListener("click", async () => {
    const contentId = contentIdInput.value;
    const resetResponse = await fetch(`/content/render-settings-form/${contentId}/`);
    if (!resetResponse.ok) {
      console.error("Failed to reset content settings form");
      return;
    }

    const newFormHTML = await resetResponse.text();
    const oldForm = document.getElementById("content-settings-form");
    oldForm.outerHTML = newFormHTML;
  });
}

function setupClipsOnlyWarning() {
  const clipsOnlyInput = document.getElementById("clips-only");
  const formGroup = document.getElementById("clips-only-form-group");
  const warning = document.getElementById("clips-only-warning");
  if (!clipsOnlyInput || !formGroup || !warning) return;

  const hasClips = clipsOnlyInput.dataset.contentHasClips === "true";

  const updateWarning = () => {
    const shouldWarn = clipsOnlyInput.checked && !hasClips;
    formGroup.classList.toggle("clips-only-invalid", shouldWarn);
    warning.hidden = !shouldWarn;
  };

  clipsOnlyInput.addEventListener("change", updateWarning);
  updateWarning();
}

function initialize() {
  setupReset();
  setupSubmit();
  setupClipsOnlyWarning();
}

initialize();
