// Direct manipulation of blur regions on the video frame.
//
// The rig (`#blur-edit-rig`) is a separate element owned by this module, not the player's
// `.blur-position` div. That separation is what makes editing possible at all: the player's blur
// is `pointer-events: none` so it never intercepts a student's clicks, and nothing may re-enable
// events on it without putting playback at risk. It also means the player is free to destroy and
// rebuild its blur div whenever the playhead leaves the annotation's window - the rig simply
// reattaches (see _ensureRig).
//
// One rule governs every gesture: a commit says "the blur belongs here, at the time I am looking
// at". It never names a position id. The server decides whether that is a new point or an
// existing one, because only the server knows where the stored points are. That is what turns
// "drag the box at a new time" into an added point rather than a silent retime of whichever
// point happened to be selected, and it is required now that the rendered box is usually a tween
// between two points and so has no single owning row.

import { clampRect, percentWithin, rectAtTime, resizeRect } from "./video-geometry.js";
import { getCSRFToken } from "./utils.js";

// Mirrors BLUR_MIN_WIDTH / BLUR_MIN_HEIGHT in core/models.py, which remains the authority - the
// server clamps whatever it is sent. Matching here is what keeps the box from jumping on release.
const MIN_WIDTH = 3;
const MIN_HEIGHT = 4;

// The box a plain click drops, matching BLUR_DEFAULT_GEOMETRY's size in core/models.py.
const DEFAULT_WIDTH = 20;
const DEFAULT_HEIGHT = 15;

// Under this much travel the user was pointing at something, not framing it.
const DRAW_THRESHOLD_PX = 8;

// Mirrors BLUR_SNAP_SECONDS in core/models.py: a playhead within a frame or two of a stored point
// is editing *that point*, not the moment between points. The server applies the same rule when
// deciding whether a write lands on an existing point, and the two have to agree - see
// _pointAtPlayhead for what goes wrong when they do not.
const SNAP_SECONDS = 0.05;

const CLAMP_LIMITS = { minWidth: MIN_WIDTH, minHeight: MIN_HEIGHT };

function applyRect(element, rect) {
  element.style.left = `${rect.x}%`;
  element.style.top = `${rect.y}%`;
  element.style.width = `${rect.width}%`;
  element.style.height = `${rect.height}%`;
}

// Where a pointer is, as a percentage of the video frame, clamped to it. Clamping here rather
// than in each gesture means a pointer dragged past the edge of the window behaves like one held
// against the edge, instead of producing coordinates that then have to be untangled.
function pointerPercent(event, frame) {
  const point = percentWithin(
    { x: event.clientX, y: event.clientY, width: 0, height: 0 },
    frame,
  );
  return {
    x: Math.min(100, Math.max(0, point.x)),
    y: Math.min(100, Math.max(0, point.y)),
  };
}

// --- the three gestures, as pure functions of where the pointer went ---------

// Drag a rectangle out of nothing, the gesture every screenshot tool has trained users on. A
// gesture too small to be a drag drops a default-size box centered on the point instead of a
// sliver, so a plain click is a usable shortcut rather than a mistake to undo.
function drawnRect(startPoint, currentPoint, threshold) {
  const width = Math.abs(currentPoint.x - startPoint.x);
  const height = Math.abs(currentPoint.y - startPoint.y);
  if (width < threshold.x && height < threshold.y) {
    return {
      x: startPoint.x - DEFAULT_WIDTH / 2,
      y: startPoint.y - DEFAULT_HEIGHT / 2,
      width: DEFAULT_WIDTH,
      height: DEFAULT_HEIGHT,
    };
  }
  return {
    x: Math.min(startPoint.x, currentPoint.x),
    y: Math.min(startPoint.y, currentPoint.y),
    width,
    height,
  };
}

// Translating by the pointer's travel, rather than centering the box on the pointer, is what lets
// a user grab a corner of the box and keep the same grip on it.
function movedRect(origin, startPoint, currentPoint) {
  return {
    x: origin.x + (currentPoint.x - startPoint.x),
    y: origin.y + (currentPoint.y - startPoint.y),
    width: origin.width,
    height: origin.height,
  };
}

// Position one blur item's timeline dots along its bar, proportionally to where each point falls
// in the item's own duration. Exported as a function rather than kept on the class because
// placeTrackItems runs over every blur on the timeline, selected or not.
//
// `timeWindow` overrides the item's stored start/end, for while a resize handle is being dragged:
// a dot's `left` is a percentage *of the item*, so as the item's width changes the dots stretch
// along with it and then snap back to their real times on release. Recomputed against the
// provisional window instead, each dot holds its own time for the whole drag.
export function placeLocators(itemElement, timeWindow) {
  const itemStart = timeWindow
    ? timeWindow.start
    : parseFloat(itemElement.dataset["start"]);
  const itemEnd = timeWindow ? timeWindow.end : parseFloat(itemElement.dataset["end"]);
  const duration = itemEnd - itemStart;
  for (const locator of itemElement.querySelectorAll(".blur-position-locator")) {
    if (!(duration > 0)) {
      locator.style.removeProperty("left");
      continue;
    }
    const offset = (parseFloat(locator.dataset["positionTime"]) - itemStart) / duration;
    // A point dragged outside the provisional window is one this resize is about to discard.
    // Hiding it previews that, rather than parking the dot outside the bar it belongs to. With
    // the stored window this cannot trigger: every point lies inside its own blur by invariant.
    locator.hidden = offset < -0.001 || offset > 1.001;
    // Halved dot width, less the bar's own inset, so the dot is centered on its time rather
    // than starting there. The closing paren used to be missing, which made CSS discard the
    // whole declaration and stack every dot at the left edge of the bar.
    const nudge = locator.getBoundingClientRect().width / 2 - 2;
    locator.style.setProperty("left", `calc(${offset * 100}% - ${nudge}px)`);
  }
}

export class BlurEditor {
  /**
   * @param {object} options
   * @param {HTMLVideoElement} options.video
   * @param {object} options.player the AnnotationPlayer instance
   * @param {HTMLElement} options.timelineWrapper delegation root for the timeline dots
   * @param {Function} options.onPositionsSaved called after a save lands, so the editor can
   *   re-place the track items whose HTML this module just replaced
   */
  constructor({ video, player, timelineWrapper, onPositionsSaved }) {
    this.video = video;
    this.player = player;
    this.annotationBox = player.annotationBox;
    this.timelineWrapper = timelineWrapper;
    this.onPositionsSaved = onPositionsSaved || (() => {});

    this.annotationId = null;
    this.startTime = 0;
    this.endTime = 0;
    this.rig = null;
    // A time to land on once selection settles, set by clicking a dot on a blur that is not the
    // selected one. See select().
    this.pendingSeek = null;
    // Non-null only while a pointer is down. Its presence stops _render from overwriting the
    // geometry the user is actively dragging.
    this.gesture = null;

    this._onBoxPointerDown = this._onBoxPointerDown.bind(this);
    this._onRigPointerDown = this._onRigPointerDown.bind(this);
    this._render = this._render.bind(this);

    // The rig tracks the playhead so it stays on top of the blur it is editing, which is also
    // what makes it show the interpolated geometry between two points.
    this.video.addEventListener("timeupdate", this._render);
    this.video.addEventListener("seeked", this._render);

    // Delegated listeners, attached once. Every save replaces the panel and the track item
    // wholesale, so per-element listeners would need re-attaching each time and any miss would
    // leave a dead control behind.
    document.addEventListener("click", this._onPanelClick.bind(this));
    if (this.timelineWrapper) {
      // Capture phase, deliberately. #timeline-wrapper is an *ancestor* of the track items, so a
      // bubbling listener here would run only after the item's own click handler had already
      // reloaded the detail form - and stopPropagation() at that point stops nothing.
      this.timelineWrapper.addEventListener(
        "click",
        this._onLocatorClick.bind(this),
        true,
      );
    }
  }

  // --- selection -------------------------------------------------------------

  select(annotationId) {
    this.annotationId = String(annotationId);
    this._readWindow();
    this.annotationBox.classList.add("annotation-box-blur-editor");
    this.annotationBox.addEventListener("pointerdown", this._onBoxPointerDown);

    const requested = this.pendingSeek;
    this.pendingSeek = null;
    if (requested !== null && requested !== undefined) {
      // Selection came from clicking one of this blur's timeline dots, so land on that point.
      // Applied here rather than at the click, because selecting an item seeks to its start -
      // and that happens after the click, so anything set earlier would be overwritten.
      this.video.currentTime = requested;
    } else if (
      this.video.currentTime < this.startTime ||
      this.video.currentTime > this.endTime
    ) {
      // There has to be something on screen to edit. With the playhead outside the blur's window
      // the frame is empty and the first gesture would have nothing to aim at.
      this.video.currentTime = this.startTime;
    }
    this._render();
  }

  deselect() {
    this.gesture?.cancel();
    this.annotationBox.removeEventListener("pointerdown", this._onBoxPointerDown);
    // Toggling the one class, rather than assigning className, so the player's
    // annotation-box-showing-message state survives a selection change.
    this.annotationBox.classList.remove("annotation-box-blur-editor");
    this.rig?.remove();
    this.rig = null;
    this.annotationId = null;
    // The panel rows go away with the form, but the timeline dots live in the track item and
    // outlive it - so without this the last point stays lit on a blur that is no longer selected.
    this._markPositionActive(null);
  }

  /** Re-read the panel and the item bar, for when something else on the page changed them. */
  syncFromPanel() {
    if (!this.annotationId) return;
    this._readWindow();
    this._render();
  }

  // --- reading the current state --------------------------------------------

  _itemElement() {
    return this.timelineWrapper?.querySelector(
      `.track-item[data-annotation-type="blur"][data-annotation-id="${this.annotationId}"]`,
    );
  }

  // The item bar's dataset, not the form inputs: it carries plain seconds, while the form's time
  // fields are written as HH:MM:SS by the "set time" buttons.
  _readWindow() {
    const item = this._itemElement();
    if (!item) return;
    const start = parseFloat(item.dataset["start"]);
    const end = parseFloat(item.dataset["end"]);
    if (Number.isFinite(start) && Number.isFinite(end)) {
      this.startTime = start;
      this.endTime = end;
    }
  }

  // The panel rows are the client's copy of the stored points - no parallel array to fall out of
  // step with them. The server renders them ordered by time, which is what rectAtTime requires.
  _positions() {
    const rows = document.querySelectorAll("#blur-positions-wrapper .position-entry");
    return Array.from(rows).map((row) => ({
      id: row.dataset["positionId"],
      time: parseFloat(row.dataset["time"]),
      x: parseFloat(row.dataset["x"]),
      y: parseFloat(row.dataset["y"]),
      width: parseFloat(row.dataset["width"]),
      height: parseFloat(row.dataset["height"]),
    }));
  }

  _currentRect() {
    return rectAtTime(this._positions(), this.video.currentTime);
  }

  // The stored point the playhead is sitting on, within the snap window, or null between points.
  //
  // This is what a gesture has to be measured against, and it is not the same thing as
  // _currentRect(). Parked 20ms past a point, the rig shows the interpolated rect for *that*
  // moment - a hair along the way toward the next point. Committing that rect while the server
  // snapped the write back onto the point stored it as the geometry at the point's own time,
  // which walked it a little toward its neighbour. The next drag then started from the moved
  // value and walked a little further, so the box visibly crept in width and height on every
  // release, in whichever direction the neighbouring point happened to lie.
  _pointAtPlayhead() {
    const time = this.video.currentTime;
    let nearest = null;
    for (const position of this._positions()) {
      const distance = Math.abs(position.time - time);
      if (distance <= SNAP_SECONDS && (nearest === null || distance < nearest.distance)) {
        nearest = { distance, position };
      }
    }
    return nearest && nearest.position;
  }

  // Where a gesture starts from: the point being edited, if there is one, otherwise the tween the
  // rig is showing (which a commit will turn into a new point at the playhead).
  _gestureOrigin() {
    const point = this._pointAtPlayhead();
    if (!point) return this._currentRect();
    return {
      x: point.x,
      y: point.y,
      width: point.width,
      height: point.height,
    };
  }

  // --- drawing ---------------------------------------------------------------

  _ensureRig() {
    if (this.rig?.isConnected) return this.rig;

    const rig = document.createElement("div");
    rig.id = "blur-edit-rig";
    rig.tabIndex = 0;
    rig.setAttribute("role", "group");
    rig.setAttribute("aria-label", "Blur region");
    // Same corner set, and the same CSS, as the comment box's handles.
    for (const edges of [
      "resize-point-top resize-point-left",
      "resize-point-top",
      "resize-point-left",
      "",
    ]) {
      const handle = document.createElement("div");
      handle.className = `blur-rig-handle resize-point ${edges}`.trim();
      rig.appendChild(handle);
    }
    rig.addEventListener("pointerdown", this._onRigPointerDown);
    this.annotationBox.appendChild(rig);
    this.rig = rig;
    return rig;
  }

  _paint(rect) {
    applyRect(this._ensureRig(), rect);
    // Paint the player's blur too, so a drag previews the blurred result instead of an outline
    // that the blurred pixels lag behind. The player owns that element the rest of the time and
    // recomputes it from stored data on its next pass - which is also what silently undoes a
    // cancelled gesture, with no saved-state bookkeeping here.
    const blur = this.annotationBox.querySelector(`#blur-overlay-${this.annotationId}`);
    if (blur) applyRect(blur, rect);
  }

  _render() {
    if (!this.annotationId || this.gesture) return;
    const rig = this._ensureRig();
    const time = this.video.currentTime;
    const rect =
      time >= this.startTime && time <= this.endTime ? this._currentRect() : null;
    // Hidden rather than removed outside the blur's window: an editable box at a time when the
    // blur does not exist would invite an edit that cannot be stored.
    rig.hidden = rect === null;
    if (rect) this._paint(rect);

    // Highlight whichever point the playhead is on, in both the panel and the timeline. Derived
    // from the playhead rather than remembered from the last click, so it stays right when the
    // form is reloaded (which replaces the rows, losing any class set on them), when a save
    // rebuilds the panel, and when the user simply scrubs onto a point.
    this._markPositionActive(this._pointAtPlayhead()?.id);
  }

  // --- gestures --------------------------------------------------------------

  _onBoxPointerDown(event) {
    if (!this.annotationId || event.button !== 0 || this.rig?.hidden) return;
    // Anywhere on the frame that is not the existing box: frame a new region there.
    const frame = this.annotationBox.getBoundingClientRect();
    const threshold = {
      x: (DRAW_THRESHOLD_PX / frame.width) * 100,
      y: (DRAW_THRESHOLD_PX / frame.height) * 100,
    };
    this._beginGesture(event, (startPoint, currentPoint) =>
      drawnRect(startPoint, currentPoint, threshold),
    );
  }

  _onRigPointerDown(event) {
    if (!this.annotationId || event.button !== 0) return;
    // Without this the annotation box would also start a draw underneath the move.
    event.stopPropagation();
    const origin = this._gestureOrigin();
    if (!origin) return;

    const handle = event.target.closest(".blur-rig-handle");
    if (handle) {
      const options = {
        movesLeft: handle.classList.contains("resize-point-left"),
        movesTop: handle.classList.contains("resize-point-top"),
        ...CLAMP_LIMITS,
      };
      this._beginGesture(event, (_startPoint, currentPoint) =>
        resizeRect(origin, currentPoint, options),
      );
    } else {
      this._beginGesture(event, (startPoint, currentPoint) =>
        movedRect(origin, startPoint, currentPoint),
      );
    }
  }

  _beginGesture(event, computeRect) {
    const frame = this.annotationBox.getBoundingClientRect();
    if (!(frame.width > 0) || !(frame.height > 0)) return;
    event.preventDefault();

    // A moving picture cannot be aimed at, and the time a commit lands on is whatever the
    // playhead reads on release - so freeze it. Pausing also settles the one race in this file:
    // applyAnnotations only reschedules itself while playing, so a paused player will not fight
    // the geometry being painted below. Playback is deliberately not resumed afterwards; it
    // would immediately glide the box away from where the user just put it.
    if (!this.video.paused) this.video.pause();

    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    const startPoint = pointerPercent(event, frame);
    const rectAt = (pointerEvent) =>
      clampRect(computeRect(startPoint, pointerPercent(pointerEvent, frame)), CLAMP_LIMITS);

    const cleanup = () => {
      if (target.hasPointerCapture(event.pointerId)) {
        target.releasePointerCapture(event.pointerId);
      }
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
      target.removeEventListener("pointercancel", cancel);
      document.removeEventListener("keydown", onKeyDown);
      this.gesture = null;
    };

    const onMove = (moveEvent) => this._paint(rectAt(moveEvent));

    const onUp = (upEvent) => {
      const rect = rectAt(upEvent);
      cleanup();
      this._commit(rect);
    };

    const cancel = () => {
      cleanup();
      // Nothing to restore by hand: re-rendering from the stored points is the undo.
      this._render();
    };

    const onKeyDown = (keyEvent) => {
      if (keyEvent.key !== "Escape") return;
      // The player seeks on bare keystrokes from a document-level listener.
      keyEvent.stopPropagation();
      keyEvent.preventDefault();
      cancel();
    };

    this.gesture = { cancel };
    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", cancel);
    document.addEventListener("keydown", onKeyDown);
  }

  // --- persistence -----------------------------------------------------------

  /**
   * Write geometry for this blur at the playhead.
   *
   * @param {object} rect in frame percentages
   * @param {object} [options]
   * @param {string} [options.positionId] only for gestures that name a point rather than a time -
   *   the numeric inputs and dragging a timeline dot. A drag on the frame must never send one.
   * @param {number} [options.time] only alongside positionId, to retime that point.
   */
  async _commit(rect, { positionId, time } = {}) {
    const annotationId = this.annotationId;
    if (!annotationId) return false;

    // The point's own time when one is being edited, so client and server cannot disagree about
    // which moment this write belongs to. Sending the raw playhead and letting the server snap it
    // meant the geometry described one instant and was filed under another.
    const editing = positionId === undefined ? this._pointAtPlayhead() : null;
    const body = {
      time:
        time !== undefined
          ? time
          : editing
            ? editing.time
            : Math.round(this.video.currentTime * 100) / 100,
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
    };
    if (positionId !== undefined) body.position_id = positionId;

    let response;
    try {
      response = await fetch(`/annotations/blur/${annotationId}/positions/`, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (error) {
      console.error("Failed to reach the server to save a blur point", error);
      this._render();
      return false;
    }

    if (!response.ok) {
      console.error(`Failed to save blur point (${response.status})`);
      // Put the box back where the stored points say it is, rather than leaving the user looking
      // at geometry that was not saved.
      this._render();
      return false;
    }

    this._applySaved(annotationId, await response.json());
    return true;
  }

  async _delete(positionId) {
    const annotationId = this.annotationId;
    const response = await fetch(`/annotations/blur/positions/${positionId}/`, {
      method: "DELETE",
      headers: { "X-CSRFToken": getCSRFToken() },
    });
    if (!response.ok) {
      console.error(`Failed to delete blur point (${response.status})`);
      return false;
    }
    this._applySaved(annotationId, await response.json());
    return true;
  }

  _applySaved(annotationId, payload) {
    // The user may have selected something else while the request was in flight, in which case
    // this response describes a panel that is no longer on screen.
    if (annotationId !== this.annotationId) return;

    const wrapper = document.getElementById("blur-positions-wrapper");
    if (wrapper && payload["blurPositions"]) {
      wrapper.outerHTML = payload["blurPositions"];
    }

    const item = this._itemElement();
    if (item && payload["trackItem"]) {
      const wasActive = item.classList.contains("active-track-item");
      item.outerHTML = payload["trackItem"];
      // Re-applying just the class, rather than calling markItemAsActive, which would also seek
      // the playhead back to the blur's start and throw away the frame being edited.
      if (wasActive) this._itemElement()?.classList.add("active-track-item");
    }

    this.player.replaceAnnotationPositions(annotationId, payload["positions"]);
    this.onPositionsSaved(annotationId);
    this._readWindow();
    this._render();
  }

  // --- panel and timeline ----------------------------------------------------

  // A null id clears the highlight, which is the honest state between two points.
  _markPositionActive(positionId) {
    const active = positionId === null || positionId === undefined ? null : String(positionId);
    for (const [selector, activeClass] of [
      [".position-entry", "active-position-entry"],
      [".blur-position-locator", "active-blur-position-locator"],
    ]) {
      for (const element of document.querySelectorAll(selector)) {
        element.classList.toggle(
          activeClass,
          active !== null && element.dataset["positionId"] === active,
        );
      }
    }
  }

  _onPanelClick(event) {
    const deleteButton = event.target.closest(".blur-position-delete-button");
    if (deleteButton) {
      // The button lives inside the annotation form; without this the form submits.
      event.preventDefault();
      const positionId = deleteButton.closest(".position-entry")?.dataset["positionId"];
      if (positionId) this._delete(positionId);
      return;
    }

    const row = event.target.closest(".position-entry");
    if (!row) return;
    const time = parseFloat(row.dataset["time"]);
    if (Number.isFinite(time)) this.video.currentTime = time;
    this._markPositionActive(row.dataset["positionId"]);
  }

  _onLocatorClick(event) {
    const locator = event.target.closest(".blur-position-locator");
    if (!locator) return;
    const time = parseFloat(locator.dataset["positionTime"]);

    if (locator.closest(".track-item")?.classList.contains("active-track-item")) {
      // Already the selected blur, so nothing needs reloading: keep the click to ourselves rather
      // than letting the item refetch the very form that is already on screen.
      event.stopPropagation();
      if (Number.isFinite(time)) this.video.currentTime = time;
      this._markPositionActive(locator.dataset["positionId"]);
      return;
    }

    // A different blur: let the click through so the item's handler selects it and loads its
    // form, but ask select() to land on the point that was clicked instead of the blur's start.
    this.pendingSeek = Number.isFinite(time) ? time : null;
  }
}
