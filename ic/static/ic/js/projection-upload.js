// Chunked, resumable upload of a projection master.
// Each chunk is one PUT, so no single request is long enough to hit a proxy timeout,
// and an interrupted upload resumes from the server's byte count.

import { getCSRFToken } from "../../js/utils.js";

const root = document.getElementById("ic-upload");
const input = document.getElementById("ic-upload-file");
const startButton = document.getElementById("ic-upload-start");
const progress = document.getElementById("ic-upload-progress");
const statusLine = document.getElementById("ic-upload-status");
const chunkBytes = Number(root.dataset.chunkBytes);
const RETRY_DELAY_MS = 3000;
const MAX_RETRIES = 20;
const POLL_MS = 2000;

let uploading = false;

function setStatus(text, kind = "") {
  statusLine.textContent = text;
  statusLine.className = kind ? `ic-${kind}` : "";
}

function formatBytes(bytes) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  return `${(bytes / 1e6).toFixed(0)} MB`;
}

function headers(extra = {}) {
  return { "X-CSRFToken": getCSRFToken(), ...extra };
}

async function createUpload(file) {
  const response = await fetch(root.dataset.createUrl, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ filename: file.name, size: file.size }),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function sendChunk(upload, file, offset) {
  const end = Math.min(file.size, offset + chunkBytes);
  const response = await fetch(`${upload.chunk_url}?offset=${offset}`, {
    method: "PUT",
    headers: headers({ "Content-Type": "application/octet-stream" }),
    body: file.slice(offset, end),
  });
  if (response.status === 409) {
    // The server has a different byte count (e.g. a retried chunk landed); continue from it.
    const data = await response.json();
    if (typeof data.received_bytes === "number") return data.received_bytes;
  }
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  return (await response.json()).received_bytes;
}

async function waitForProcessing(upload) {
  for (;;) {
    const response = await fetch(upload.status_url, { headers: { Accept: "application/json" } });
    const data = await response.json();
    if (data.status === "done") {
      setStatus("Done. The SD editing file is being made from the master now.", "ok");
      window.setTimeout(() => (window.location.href = root.dataset.doneUrl), 2500);
      return;
    }
    if (data.status === "failed") {
      setStatus(`The master was not accepted: ${data.error}`, "error");
      return;
    }
    const job = data.job;
    setStatus(
      job && job.status === "running"
        ? "Checking the file (ffprobe, faststart, checksum)…"
        : "Waiting for the media worker…",
    );
    await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
  }
}

async function run(file) {
  uploading = true;
  startButton.disabled = true;
  progress.hidden = false;
  let upload = await createUpload(file);
  let offset = upload.received_bytes;
  let retries = 0;
  const started = performance.now();
  const startedAt = offset;
  while (offset < file.size) {
    try {
      offset = await sendChunk(upload, file, offset);
      retries = 0;
    } catch (error) {
      retries += 1;
      if (retries > MAX_RETRIES) throw error;
      setStatus(`Connection problem (${error.message}); retrying…`, "warn");
      await new Promise((resolve) => window.setTimeout(resolve, RETRY_DELAY_MS));
      continue;
    }
    const seconds = (performance.now() - started) / 1000;
    const rate = (offset - startedAt) / Math.max(seconds, 0.001);
    const remaining = rate > 0 ? (file.size - offset) / rate : 0;
    progress.value = (100 * offset) / file.size;
    setStatus(
      `${formatBytes(offset)} of ${formatBytes(file.size)} · ${formatBytes(rate)}/s · about ${Math.ceil(remaining / 60)} min left`,
    );
  }
  const response = await fetch(upload.finalize_url, { method: "POST", headers: headers() });
  upload = await response.json();
  if (!response.ok) throw new Error(upload.error || "The server did not accept the upload.");
  uploading = false;
  await waitForProcessing(upload);
}

input.addEventListener("change", () => {
  startButton.disabled = !input.files.length;
  const file = input.files[0];
  if (file) setStatus(`${file.name}: ${formatBytes(file.size)}`);
});

startButton.addEventListener("click", () => {
  const file = input.files[0];
  if (!file) return;
  run(file).catch((error) => {
    uploading = false;
    startButton.disabled = false;
    setStatus(`Upload stopped: ${error.message}. Choose the same file and press Upload to continue.`, "error");
  });
});

window.addEventListener("beforeunload", (event) => {
  if (uploading) {
    event.preventDefault();
    event.returnValue = "";
  }
});
