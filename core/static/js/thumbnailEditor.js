import { createElementFromHTMLString, displayUserMessage, getCSRFToken } from "./utils.js";

const WIDTH = 640;
const HEIGHT = 360;
const AUTO_FRAME_POSITIONS = [0.3, 0.5, 0.8];

// Crops `source` to fill a 16:9 canvas so every stored thumbnail has the same shape.
function toJpeg(source, sourceWidth, sourceHeight) {
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const scale = Math.max(WIDTH / sourceWidth, HEIGHT / sourceHeight);
  const width = sourceWidth * scale;
  const height = sourceHeight * scale;
  canvas.getContext("2d").drawImage(source, (WIDTH - width) / 2, (HEIGHT - height) / 2, width, height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
}

function seek(video, time) {
  return new Promise((resolve) => {
    video.addEventListener("seeked", resolve, { once: true });
    video.currentTime = time;
  });
}

// Follows the ARIA tabs pattern: one tab stop, and arrow keys move between tabs.
// Arrows only move focus, because choosing a tab changes the background.
// `onChoose` runs only when the user picks a tab, not for the initial selection.
function setupTabs(tablist, onChoose) {
  const tabs = [...tablist.querySelectorAll("[role=tab]")];
  function select(tab) {
    for (const each of tabs) {
      const selected = each === tab;
      each.setAttribute("aria-selected", selected);
      each.tabIndex = selected ? 0 : -1;
      document.getElementById(each.getAttribute("aria-controls")).hidden = !selected;
    }
  }
  tablist.addEventListener("click", (event) => {
    const tab = event.target.closest("[role=tab]");
    if (tab) {
      select(tab);
      onChoose(tab);
    }
  });
  tablist.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (!step) {
      return;
    }
    const visible = tabs.filter((tab) => !tab.hidden);
    const index = visible.indexOf(document.activeElement);
    const next = visible[(index + step + visible.length) % visible.length];
    next.focus();
  });
  return select;
}

function setupEditor(dialog) {
  const form = dialog.querySelector("#thumbnail-editor-form");
  const preview = form.querySelector(".thumbnail");
  const previewText = form.querySelector(".thumbnail-text");
  let imageSource = "keep";
  let imageBlob = null;

  function setImage(source, url, blob = null) {
    imageSource = source;
    imageBlob = blob;
    preview.style.backgroundImage = url ? `url("${url}")` : "none";
  }

  function useBlob(blob) {
    setImage("upload", URL.createObjectURL(blob), blob);
  }

  function updateStyles() {
    preview.style.backgroundColor = form.background_color.value;
    previewText.textContent = form.text.value;
    previewText.style.color = form.text_color.value;
    previewText.style.fontSize = `${form.text_size.value}rem`;
  }
  form.addEventListener("input", updateStyles);

  const selectTab = setupTabs(form.querySelector("[role=tablist]"), (tab) => {
    if (tab.id === "thumbnail-tab-color") {
      setImage("none", null);
    } else if (tab.id === "thumbnail-tab-youtube") {
      setImage("youtube", form.dataset.youtubeImageUrl);
    }
  });
  // A saved image covers the color, so start on the Upload tab rather than Solid color.
  if (preview.style.backgroundImage) {
    selectTab(form.querySelector("#thumbnail-tab-upload"));
  }

  form.querySelector("#thumbnail-upload").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) {
      return;
    }
    try {
      const bitmap = await createImageBitmap(file);
      useBlob(await toJpeg(bitmap, bitmap.width, bitmap.height));
    } catch {
      displayUserMessage("error", "That file could not be read as an image.");
    }
    // Lets the same file be picked again after switching away from it.
    event.target.value = "";
  });

  const video = form.querySelector("#thumbnail-video");
  if (video) {
    const scrubber = form.querySelector("#thumbnail-scrubber");
    const frames = form.querySelector("#thumbnail-frames");
    const captureFrame = () => toJpeg(video, video.videoWidth, video.videoHeight);
    const scrubTo = () => {
      video.currentTime = scrubber.value;
    };
    video.addEventListener("loadedmetadata", async () => {
      // Audio-only files have no picture and live streams no fixed length to pick from.
      if (!video.videoWidth || !Number.isFinite(video.duration)) {
        // Otherwise the unused panel still reserves its height in the dialog.
        form.querySelector("#thumbnail-panel-frame").remove();
        return;
      }
      for (const position of AUTO_FRAME_POSITIONS) {
        await seek(video, video.duration * position);
        const blob = await captureFrame();
        const button = document.createElement("button");
        button.type = "button";
        button.className = "thumbnail-frame";
        button.setAttribute("aria-label", `Use frame at ${Math.round(position * 100)}%`);
        const image = document.createElement("img");
        image.src = URL.createObjectURL(blob);
        image.alt = "";
        button.append(image);
        button.addEventListener("click", () => useBlob(blob));
        frames.append(button);
      }
      // Measured in seconds so each arrow-key step moves a fixed tenth of a second.
      scrubber.max = video.duration;
      scrubber.value = video.duration / 2;
      scrubTo();
      form.querySelector("#thumbnail-tab-frame").hidden = false;
    }, { once: true });
    scrubber.addEventListener("input", scrubTo);
    const useCurrentFrame = async () => {
      if (video.seeking) {
        await new Promise((resolve) => video.addEventListener("seeked", resolve, { once: true }));
      }
      useBlob(await captureFrame());
    };
    form.querySelector("#thumbnail-capture").addEventListener("click", useCurrentFrame);
    video.addEventListener("click", useCurrentFrame);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const saveButton = event.submitter;
    const data = new FormData(form);
    data.set("image_source", imageSource);
    if (imageBlob) {
      data.set("image", imageBlob, "thumbnail.jpg");
    }
    saveButton.disabled = true;
    try {
      const response = await fetch(form.dataset.saveUrl, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
        body: data,
      });
      if (!response.ok) {
        displayUserMessage("error", (await response.text()) || "Failed to save thumbnail.");
        return;
      }
      dialog.close("saved");
    } catch {
      displayUserMessage("error", "Could not reach the server. Check your connection and try again.");
    } finally {
      saveButton.disabled = false;
    }
  });
}

// Resolves to true if the thumbnail was saved, once the editor closes.
export async function openThumbnailEditor(url) {
  let html;
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(response.statusText);
    }
    html = await response.text();
  } catch {
    displayUserMessage("error", "Failed to load the thumbnail editor.");
    return false;
  }
  const dialog = createElementFromHTMLString(html);
  document.body.append(dialog);
  setupEditor(dialog);
  dialog.showModal();
  await new Promise((resolve) => dialog.addEventListener("close", resolve, { once: true }));
  dialog.remove();
  return dialog.returnValue === "saved";
}

let editorOpen = false;

// Delegated because the playlist settings form, which holds a button, is re-rendered in place.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-thumbnail-editor-url]");
  if (!button || editorOpen) {
    return;
  }
  editorOpen = true;
  const saved = await openThumbnailEditor(button.dataset.thumbnailEditorUrl);
  editorOpen = false;
  if (saved) {
    location.reload();
  }
});
