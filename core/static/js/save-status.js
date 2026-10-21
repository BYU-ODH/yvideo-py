// The "Waiting to autosave..." / "Changes saved" line under the annotation set title.
//
// Every editor request that persists a change goes through saveStatus.fetch, so the indicator
// cannot miss a save. Edits that are held back before being sent (debounced form input, keyboard
// nudges of a blur) register with wait(key) so the line reads as pending from the moment the user
// sees the change, not only once the request leaves.

const WAITING_TEXT = "Waiting to autosave...";
const SAVED_TEXT = "Changes saved";
const FAILED_TEXT = "Save failed";

class SaveStatus {
  constructor() {
    this.waitingKeys = new Set();
    this.inFlight = 0;
    this.requestedSinceIdle = false;
    this.failedSinceIdle = false;
    this.settledText = "";
    this.renderQueued = false;
  }

  wait(key) {
    this.waitingKeys.add(key);
    this._queueRender();
  }

  settle(key) {
    this.waitingKeys.delete(key);
    this._queueRender();
  }

  async fetch(url, options) {
    this.inFlight++;
    this.requestedSinceIdle = true;
    this._queueRender();
    try {
      const response = await fetch(url, options);
      if (!response.ok) this.failedSinceIdle = true;
      return response;
    } catch (error) {
      this.failedSinceIdle = true;
      throw error;
    } finally {
      this.inFlight--;
      this._queueRender();
    }
  }

  // Deferred so that settling a wait and starting its request in the same task reads as one
  // continuous pending state, rather than a "saved" flash and check mark in between.
  _queueRender() {
    if (this.renderQueued) return;
    this.renderQueued = true;
    queueMicrotask(() => {
      this.renderQueued = false;
      this._render();
    });
  }

  _render() {
    const busy = this.inFlight > 0 || this.waitingKeys.size > 0;
    let justSaved = false;
    if (!busy && this.requestedSinceIdle) {
      this.settledText = this.failedSinceIdle ? FAILED_TEXT : SAVED_TEXT;
      justSaved = !this.failedSinceIdle;
      this.requestedSinceIdle = false;
      this.failedSinceIdle = false;
    }
    const text = busy ? WAITING_TEXT : this.settledText;

    const element = document.getElementById("annotation-save-status");
    if (!element) return;
    const textElement = element.querySelector(".save-status-text");
    // Unchanged text is left alone so the live region does not re-announce it.
    if (textElement.textContent !== text) textElement.textContent = text;
    element.classList.toggle("save-status-failed", text === FAILED_TEXT);

    if (justSaved) {
      const check = element.querySelector(".save-status-check");
      check.classList.remove("save-status-check-animating");
      // Forces a reflow so back-to-back saves restart the animation instead of continuing it.
      void check.getBoundingClientRect();
      check.classList.add("save-status-check-animating");
    }
  }
}

export const saveStatus = new SaveStatus();
