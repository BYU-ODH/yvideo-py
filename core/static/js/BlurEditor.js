// Direct manipulation of blur regions on the video frame.
//
// The rig (`#blur-edit-rig`) is a separate element from the player's `.blur-position` div, which is
// `pointer-events: none` so it never intercepts a student's clicks and which the player destroys and
// rebuilds whenever the playhead leaves the annotation's window.
//
// A commit says "the blur belongs here, at the time I am looking at" and never names a position id:
// only the server knows where the stored points are, so only it can decide whether that is a new
// point or an existing one, and the rendered box is usually a tween with no single owning row.
//
// Two things this module deliberately does not hold its own copy of, because the server sends both
// and a second copy is a second thing to keep in step:
//   - endpoints, which arrive as `data-positions-url` on the track item and `data-delete-url` on
//     each point (rendered by reverse(), so a route rename in core/urls.py travels with them);
//   - the wording of a refused write, which is the response body - see serverMessage.
// The numeric constants below are the exception, and core/tests/test_js_constant_parity.py holds
// them to the values in core/models.py.

import {
  RESIZE_HANDLES,
  clampRect,
  edgesForHandle,
  percentWithin,
  rectAtTime,
  resizeRect,
} from "./video-geometry.js";
import {
  animateDuringPlayback,
  applyRect,
  createElementFromHTMLString,
  formatSecondsToString,
  getCSRFToken,
  parseTimeStringToSeconds,
} from "./utils.js";

// Mirrors BLUR_MIN_WIDTH / BLUR_MIN_HEIGHT in core/models.py, which the server clamps against.
// Matching here keeps the box from jumping on release.
const MIN_WIDTH = 3;
const MIN_HEIGHT = 4;

// Mirrors BLUR_SNAP_SECONDS in core/models.py: a playhead within a frame or two of a stored point
// is editing *that point*, not the moment between points. The server applies the same rule when
// deciding whether a write lands on an existing point, and the two have to agree.
const SNAP_SECONDS = 0.05;

const CLAMP_LIMITS = { minWidth: MIN_WIDTH, minHeight: MIN_HEIGHT };

const NUDGE_PERCENT = 0.5;
const NUDGE_PERCENT_LARGE = 5;
// Arrow keys arrive in bursts, so a request per keystroke would flood the endpoint and store every
// intermediate position as though the user had meant to stop there.
const NUDGE_COMMIT_MS = 400;
// The arrow keys the player uses for frame-stepping belong to the box once the rig has focus, so
// `,` and `.` replace them.
const SCRUB_SECONDS = 0.1;
// Times are stored to 2dp, so anything closer than half of that last digit is the same instant
// expressed differently, not a change.
const SAME_TIME_SECONDS = 0.005;
const POSITION_INPUTS = [
  ["position-time-input", "time"],
  ["position-x-input", "x"],
  ["position-y-input", "y"],
  ["position-width-input", "width"],
  ["position-height-input", "height"],
];
const POSITION_INPUT_SELECTOR = POSITION_INPUTS.map(([name]) => `.${name}`).join(", ");

// How each column reads and writes its field: the time column speaks H:MM:SS.SS, the geometry
// columns plain percentages. `noun` is what the field is called when what was typed is rejected.
const TIME_FIELD = {
  parse: parseTimeStringToSeconds,
  format: formatSecondsToString,
  noun: "time",
};
const NUMBER_FIELD = { parse: parseFloat, format: String, noun: "number" };

// Long enough for the sentences the blur-position views word, short enough that anything which is
// really a page rather than a message is rejected. See serverMessage.
const MAX_SERVER_MESSAGE_LENGTH = 200;

/**
 * The message to show for a failed request: the server's own words when it wrote any.
 *
 * The blur-position views word their 4xx bodies for the user (see BLUR_POSITION_* in
 * core/views_video_editor.py), so repeating that wording here would be a second copy of one string
 * to keep in step - and the copy the user actually reads. A new refusal added server-side then
 * reports itself correctly rather than as whatever the client last guessed.
 *
 * 5xx and network failures are the other way round: the body is a stack trace, a proxy's error page,
 * or nothing, so the caller's wording is all there is.
 */
async function serverMessage(response, fallback) {
  if (response.status < 400 || response.status >= 500) return fallback;
  let body;
  try {
    body = (await response.text()).trim();
  } catch {
    return fallback;
  }
  // Markup means Django's debug page or something in front of it answered, not one of our views.
  if (!body || body.startsWith("<") || body.length > MAX_SERVER_MESSAGE_LENGTH) {
    return fallback;
  }
  return body;
}

// Clamping here rather than in each gesture means a pointer dragged past the edge of the window
// behaves like one held against the edge.
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

// `timeWindow` overrides the item's stored start/end while a resize handle is being dragged: a
// dot's `left` is a percentage *of the item*, so against the stored window the dots would stretch
// with the item's changing width and then snap back on release. Recomputed against the provisional
// window, each dot holds its own time for the whole drag.
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
    // A point dragged outside the provisional window is one this resize is about to discard, so
    // hiding it previews that. Against the stored window this cannot trigger: every point lies
    // inside its own blur by invariant.
    locator.hidden = offset < -0.001 || offset > 1.001;
    // Halved dot width, less the bar's own inset, so the dot is centered on its time rather than
    // starting there.
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
    this.pendingPoint = null;
    this.selectedPositionId = null;
    // Non-null only while a pointer is down; its presence stops _render from overwriting the
    // geometry the user is actively dragging.
    this.gesture = null;
    // A painted-but-unwritten keyboard nudge. Held separately from `gesture` because the two end
    // differently: a cancelled drag is discarded, but a nudge the user can see is an edit they
    // made, so it is flushed.
    this.nudge = null;
    this.draggingLocator = false;

    this._onRigPointerDown = this._onRigPointerDown.bind(this);
    this._onRigKeyDown = this._onRigKeyDown.bind(this);
    this._render = this._render.bind(this);

    // Two cadences, the same split the editor's scrubber and the player's progress bar already use:
    // `timeupdate`/`seeked` cover scrubbing and the paused case, while animateDuringPlayback runs
    // every animation frame, the rate at which the player moves the blur underneath the rig.
    this.video.addEventListener("timeupdate", this._render);
    this.video.addEventListener("seeked", this._render);
    this._stopPlaybackTracking = animateDuringPlayback(this.video, this._render);
    // A pending nudge carries the time it was made at, so it can be written out on a seek rather
    // than holding the rig on a rect from a frame that is no longer on screen.
    this.video.addEventListener("seeking", () => this._flushNudge());

    // Delegated, because every save replaces the panel rows and the track item, so per-element
    // listeners would need re-attaching each time.
    document.addEventListener("click", this._onPanelClick.bind(this));
    document.addEventListener("pointerdown", this._onPanelPointerDown.bind(this));
    document.addEventListener("change", this._onPanelInputChange.bind(this));
    document.addEventListener("keydown", this._onPanelInputKeyDown.bind(this));
    if (this.timelineWrapper) {
      // Capture phase, deliberately. #timeline-wrapper is an *ancestor* of the track items, so a
      // bubbling listener would run only after the item's own click handler had already reloaded
      // the detail form - and stopPropagation() at that point stops nothing.
      this.timelineWrapper.addEventListener(
        "click",
        this._onLocatorClick.bind(this),
        true,
      );
      this.timelineWrapper.addEventListener(
        "pointerdown",
        this._onLocatorPointerDown.bind(this),
        true,
      );
      this.timelineWrapper.addEventListener(
        "keydown",
        this._onLocatorKeyDown.bind(this),
        true,
      );
      // A dot sits inside a `draggable="true"` track item, so a native drag that escapes the dot
      // gesture moves the whole annotation along the timeline instead of retiming one point.
      // Taking the pointer suppresses it in practice; this is the backstop, since neither
      // setPointerCapture nor preventDefault on pointerdown is specified to.
      this.timelineWrapper.addEventListener(
        "dragstart",
        (event) => {
          if (this.draggingLocator) event.preventDefault();
        },
        true,
      );
    }
  }

  // --- selection -------------------------------------------------------------

  select(annotationId) {
    this.annotationId = String(annotationId);
    this._readWindow();
    this.annotationBox.classList.add("annotation-box-blur-editor");

    const requested = this.pendingPoint;
    this.pendingPoint = null;
    if (requested) {
      this.video.currentTime = requested.time;
      this._selectPosition(requested.positionId);
    } else if (
      this.video.currentTime < this.startTime ||
      this.video.currentTime > this.endTime
    ) {
      // There has to be a box on screen to grab: outside the blur's window the frame is empty.
      this.video.currentTime = this.startTime;
    }
    this._render();
  }

  deselect() {
    // Flushed, not cancelled: a nudge is already visible on screen, so discarding it would throw
    // away an edit the user made. _commit captures the annotation id synchronously, so it is still
    // addressed to the blur being deselected.
    this._flushNudge();
    this.gesture?.cancel();
    // Toggling the one class, rather than assigning className, so the player's
    // annotation-box-showing-message state survives a selection change.
    this.annotationBox.classList.remove("annotation-box-blur-editor");
    this.rig?.remove();
    this.rig = null;
    this.annotationId = null;
    // The panel rows go away with the form, but the timeline dots live in the track item and
    // outlive it, so the last point would stay lit on a blur that is no longer selected.
    this._selectPosition(null);
  }

  syncFromPanel() {
    if (!this.annotationId) return;
    this._readWindow();
    this._render();
    this._paintSelectedPosition();
  }

  // Saving an annotation writes a new version with a new id, so the selected blur's item bar and
  // panel are re-rendered under that id. Follow it rather than re-running select(), which would
  // seek the video out from under an edit in progress.
  retarget(annotationId) {
    if (!this.annotationId) return;
    this.annotationId = String(annotationId);
    this.syncFromPanel();
  }

  // --- reading the current state --------------------------------------------

  // Takes an id rather than always reading this.annotationId, because a save can outlive its
  // selection and _applySaved still has to find the bar the response describes.
  _itemElement(annotationId = this.annotationId) {
    return this.timelineWrapper?.querySelector(
      `.track-item[data-annotation-type="blur"][data-annotation-id="${annotationId}"]`,
    );
  }

  // The item bar's dataset, not the form inputs: the bar is what the server just re-rendered,
  // while the form's fields are whatever the user has typed so far.
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

  // The server renders these rows ordered by time, which is what rectAtTime requires.
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

  // The stored point `time` is sitting on, within the snap window, or null between points.
  //
  // A gesture has to be measured against this rather than against _currentRect(): parked 20ms past a
  // point, the rig shows an interpolated rect a hair along the way toward the next point, and
  // committing that under the point's own time would walk the box toward its neighbour on every
  // release.
  //
  // Takes a time rather than reading the playhead, because a keyboard nudge is written after a
  // delay and has to be filed under the moment it was made at.
  _pointAt(time) {
    let nearest = null;
    for (const position of this._positions()) {
      const distance = Math.abs(position.time - time);
      if (distance <= SNAP_SECONDS && (nearest === null || distance < nearest.distance)) {
        nearest = { distance, position };
      }
    }
    return nearest && nearest.position;
  }

  _gestureOrigin() {
    // An unwritten nudge is still what the user can see, so it is what the next gesture builds on -
    // otherwise grabbing the box after arrowing it would snap it back.
    if (this.nudge) return { ...this.nudge.rect };
    const point = this._pointAt(this.video.currentTime);
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
    rig.setAttribute("aria-keyshortcuts", "ArrowUp ArrowDown ArrowLeft ArrowRight Comma Period");
    rig.title =
      "Drag to move, handles to resize. Arrow keys nudge (Shift for bigger steps); " +
      ", and . step the video.";
    for (const [name] of RESIZE_HANDLES) {
      const handle = document.createElement("div");
      handle.className = "overlay-resize-handle blur-rig-handle";
      handle.dataset["handle"] = name;
      rig.appendChild(handle);
    }
    rig.addEventListener("pointerdown", this._onRigPointerDown);
    rig.addEventListener("keydown", this._onRigKeyDown);
    this.annotationBox.appendChild(rig);
    this.rig = rig;
    return rig;
  }

  _paint(rect) {
    const rig = this._ensureRig();
    applyRect(rig, rect);
    // A gesture can start with the rig hidden - dragging a dot seeks the playhead into the blur's
    // window - and a gesture that paints an invisible box looks broken.
    rig.hidden = false;
    // Paint the player's blur too, so a drag previews the blurred result instead of an outline the
    // blurred pixels lag behind. The player owns that element the rest of the time and recomputes
    // it from stored data on its next pass, which is also what silently undoes a cancelled gesture.
    const blur = this.annotationBox.querySelector(`#blur-overlay-${this.annotationId}`);
    if (blur) applyRect(blur, rect);
  }

  // Draw the rig around wherever the blur is at the playhead. Runs every animation frame during
  // playback, because the player interpolates its blur at that rate and a rig that only moved on
  // `timeupdate` trails visibly behind the region it is drawn around - so this has to stay cheap.
  // The selection is not touched here; it is painted where the rows change, not where time passes.
  _render() {
    if (!this.annotationId || this.gesture || this.nudge) return;
    const rig = this._ensureRig();
    const time = this.video.currentTime;
    const rect =
      time >= this.startTime && time <= this.endTime ? this._currentRect() : null;
    // Hidden rather than removed outside the blur's window: an editable box at a time when the blur
    // does not exist would invite an edit that cannot be stored.
    rig.hidden = rect === null;
    // Only the rig - not _paint, which also writes the player's blur element. Doing that per frame
    // would put two writers on one element: the player paints it from its own copy of the positions
    // while this reads the panel's data-* rows, so any disagreement would show up as a flicker.
    if (rect) applyRect(rig, rect);
  }

  // --- gestures --------------------------------------------------------------

  _onRigPointerDown(event) {
    if (!this.annotationId || event.button !== 0) return;
    const origin = this._gestureOrigin();
    if (!origin) return;

    const handle = event.target.closest(".blur-rig-handle");
    if (handle) {
      const edges = edgesForHandle(handle.dataset["handle"]);
      if (!edges) return;
      const options = { ...edges, ...CLAMP_LIMITS };
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

    // A commit lands on whatever time the playhead reads on release, so freeze it. Pausing also
    // settles the one race in this file: applyAnnotations only reschedules itself while playing, so
    // a paused player will not fight the geometry being painted below. Playback is deliberately not
    // resumed afterwards - it would immediately glide the box away from where the user put it.
    if (!this.video.paused) this.video.pause();

    // Dropped rather than flushed: the gesture starts from the nudged rect (see _gestureOrigin) and
    // will write it anyway, so flushing would spend a request storing an intermediate position.
    this._discardNudge();

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

  // --- keyboard --------------------------------------------------------------

  _nudge(dx, dy) {
    // Outside the blur's window the rig is hidden, so a nudge there would be an edit to something
    // the user cannot see - which the server would then clamp onto the nearest end of the window.
    if (!this.rig || this.rig.hidden) return;
    const origin = this._gestureOrigin();
    if (!origin) return;

    const rect = clampRect(
      { x: origin.x + dx, y: origin.y + dy, width: origin.width, height: origin.height },
      CLAMP_LIMITS,
    );
    this._paint(rect);

    // Held against _render for as long as more keys may arrive: a timeupdate in the gap would
    // otherwise repaint from the stored points and undo the nudge halfway through it.
    //
    // The time is captured now, not read at flush time: a nudge describes the frame that was on
    // screen when the key was pressed, and the playhead can move during the commit delay.
    if (this.nudge) clearTimeout(this.nudge.timer);
    this.nudge = {
      rect,
      time: this.nudge ? this.nudge.time : Math.round(this.video.currentTime * 100) / 100,
      timer: setTimeout(() => this._flushNudge(), NUDGE_COMMIT_MS),
    };
  }

  _flushNudge() {
    const pending = this.nudge;
    if (!pending) return;
    this._discardNudge();
    this._commit(pending.rect, { time: pending.time });
  }

  /** Forget a pending nudge without writing it. Does not repaint - callers decide that. */
  _discardNudge() {
    if (!this.nudge) return;
    clearTimeout(this.nudge.timer);
    this.nudge = null;
  }

  _onRigKeyDown(event) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const step = event.shiftKey ? NUDGE_PERCENT_LARGE : NUDGE_PERCENT;

    switch (event.key) {
      case "ArrowLeft":
        this._nudge(-step, 0);
        break;
      case "ArrowRight":
        this._nudge(step, 0);
        break;
      case "ArrowUp":
        this._nudge(0, -step);
        break;
      case "ArrowDown":
        this._nudge(0, step);
        break;
      case ",":
        this._scrub(-SCRUB_SECONDS);
        break;
      case ".":
        this._scrub(SCRUB_SECONDS);
        break;
      case "Enter":
        // Writes a pending nudge without waiting out the commit delay. On a box sitting still there
        // is nothing to do: a point already exists wherever the box has been adjusted.
        this._flushNudge();
        break;
      case "Escape":
        this._discardNudge();
        this._render();
        this.rig?.blur();
        break;
      default:
        return;
    }

    // Every key handled above is one the player also binds, from a listener on `document` that only
    // steps aside for inputs and textareas.
    event.preventDefault();
    event.stopPropagation();
  }

  _scrub(delta) {
    // Flushed first, because the nudge belongs to the frame the user was looking at, not the one
    // they are moving to.
    this._flushNudge();
    // Clamped to the blur's own window, since leaving it would hide the thing being edited.
    this.video.currentTime = Math.min(
      this.endTime,
      Math.max(this.startTime, this.video.currentTime + delta),
    );
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

    // From the bar the server rendered, not built here: see the data-positions-url comment in
    // core/templates/core/partials/item.html. Its absence means the bar for this blur is gone, in
    // which case there is nothing to save against.
    const url = this._itemElement(annotationId)?.dataset["positionsUrl"];
    if (!url) {
      console.error(`No blur-position endpoint on the bar for annotation ${annotationId}`);
      return false;
    }

    const requested =
      time !== undefined ? time : Math.round(this.video.currentTime * 100) / 100;
    // Send the point's own time when one is being edited, so client and server cannot disagree
    // about which moment this write belongs to.
    const editing = positionId === undefined ? this._pointAt(requested) : null;
    const body = {
      time: editing ? editing.time : requested,
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
    };
    if (positionId !== undefined) body.position_id = positionId;

    // Where this point sat before the write, so the status line can tell a retime from a resize by
    // comparing against where it ended up.
    const previousTime =
      positionId === undefined
        ? editing?.time
        : this._positions().find((position) => position.id === String(positionId))?.time;

    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (error) {
      console.error("Failed to reach the server to save a blur point", error);
      this._reportFailure(annotationId, "The blur point could not be saved - no reply from the server.");
      return false;
    }

    if (!response.ok) {
      console.error(`Failed to save blur point (${response.status})`);
      this._reportFailure(
        annotationId,
        await serverMessage(response, "That blur point could not be saved."),
      );
      return false;
    }

    const payload = await response.json();
    if (this._applySaved(annotationId, payload)) {
      const at = formatSecondsToString(payload["time"]);
      const retimed =
        previousTime !== undefined &&
        Math.abs(payload["time"] - previousTime) > SAME_TIME_SECONDS;
      this._status(
        payload["created"]
          ? `Point added at ${at}`
          : retimed
            ? `Point moved to ${at}`
            : `Point updated at ${at}`,
      );
    }
    return true;
  }

  /**
   * @param {HTMLElement} control the panel's delete button or the point's timeline dot, whichever
   *   was used. Each carries its own `data-delete-url`, rendered by reverse() - see
   *   blur_positions.html. Taking the control rather than an id is what keeps the URL out of this
   *   file, and means a point the server will refuse to delete offers nothing to delete it with.
   */
  async _delete(control) {
    const annotationId = this.annotationId;
    const url = control?.dataset["deleteUrl"];
    if (!url) {
      console.error("No delete endpoint on the control for the blur point being deleted");
      return false;
    }
    const response = await fetch(url, {
      method: "DELETE",
      headers: { "X-CSRFToken": getCSRFToken() },
    });
    if (!response.ok) {
      console.error(`Failed to delete blur point (${response.status})`);
      this._status(await serverMessage(response, "That blur point could not be deleted."));
      return false;
    }
    if (this._applySaved(annotationId, await response.json())) {
      this._status("Point deleted");
    }
    return true;
  }

  // Addressed to the blur the request was made for, because a request can outlive its selection:
  // reporting into whichever panel is on screen would put an error about one annotation in front of
  // another.
  _reportFailure(annotationId, message) {
    if (annotationId !== this.annotationId) return;
    this._status(message);
    this._render();
  }

  /** @returns {boolean} whether the response was still describing the selected blur. */
  _applySaved(annotationId, payload) {
    // Canonical state first, and unconditionally: the save happened, so the player's positions and
    // the bar's locators have to reflect it even if the selection has already moved on - deselect()
    // flushes a pending nudge, so a saved edit routinely lands after annotationId has been cleared.
    const item = this._itemElement(annotationId);
    if (item && payload["trackItem"]) {
      const wasActive = item.classList.contains("active-track-item");
      item.outerHTML = payload["trackItem"];
      // Re-applying just the class, rather than calling markItemAsActive, which would also seek the
      // playhead back to the blur's start and throw away the frame being edited.
      if (wasActive) this._itemElement(annotationId)?.classList.add("active-track-item");
    }

    this.player.replaceAnnotationPositions(annotationId, payload["positions"]);
    // After the bar is replaced, because it re-places the locators from that DOM.
    this.onPositionsSaved(annotationId);

    // Everything below is specific to the blur on screen: the panel is a singleton belonging to
    // whatever is selected, and the rig paints only the selection.
    if (annotationId !== this.annotationId) return false;

    // Only the table, not the whole wrapper: replacing the status line would re-insert a live region
    // with its message already inside it, which screen readers generally do not announce. The
    // full-wrapper path is the fallback for an unrecognised payload shape.
    const incomingList = payload["blurPositions"]
      ? createElementFromHTMLString(payload["blurPositions"])?.querySelector("#positions-list")
      : null;
    const list = document.getElementById("positions-list");
    if (incomingList && list) {
      list.replaceWith(incomingList);
    } else {
      const wrapper = document.getElementById("blur-positions-wrapper");
      if (wrapper && payload["blurPositions"]) wrapper.outerHTML = payload["blurPositions"];
    }

    this._readWindow();
    this._render();
    this._paintSelectedPosition();
    return true;
  }

  // --- panel and timeline ----------------------------------------------------

  _status(message) {
    const status = document.getElementById("blur-position-status");
    if (status) status.textContent = message;
  }

  _goToPoint(positionId, time) {
    const seconds = parseFloat(time);
    if (Number.isFinite(seconds)) this.video.currentTime = seconds;
    this._selectPosition(positionId);
  }

  _selectPosition(positionId) {
    this.selectedPositionId =
      positionId === null || positionId === undefined ? null : String(positionId);
    this._paintSelectedPosition();
  }

  _paintSelectedPosition() {
    let found = false;
    for (const [selector, activeClass] of [
      [".position-entry", "active-position-entry"],
      [".blur-position-locator", "active-blur-position-locator"],
    ]) {
      for (const element of document.querySelectorAll(selector)) {
        const selected =
          this.selectedPositionId !== null &&
          element.dataset["positionId"] === this.selectedPositionId;
        element.classList.toggle(activeClass, selected);
        found = found || selected;
      }
    }
    if (!found) this.selectedPositionId = null;
  }

  _onPanelClick(event) {
    const deleteButton = event.target.closest(".blur-position-delete-button");
    if (!deleteButton) return;
    // The button lives inside the annotation form; without this the form submits.
    event.preventDefault();
    this._delete(deleteButton);
  }

  _onPanelPointerDown(event) {
    if (event.button !== 0 || event.target.closest(".blur-position-delete-button")) return;
    const row = event.target.closest(".position-entry");
    if (!row) return;
    this._goToPoint(row.dataset["positionId"], row.dataset["time"]);
  }

  // The panel's fields name a point by id, so a numeric edit lands on that exact row wherever the
  // playhead happens to be - the mirror image of a gesture on the frame, which names a moment and
  // lets the server work out which row that is.
  _onPanelInputChange(event) {
    const input = event.target;
    if (!this.annotationId || !input.matches?.(POSITION_INPUT_SELECTOR)) return;
    const row = input.closest(".position-entry");
    if (!row) return;

    const field = POSITION_INPUTS.find(([name]) => input.classList.contains(name))?.[1];
    if (!field) return;
    const entered = input.value;
    const column = field === "time" ? TIME_FIELD : NUMBER_FIELD;
    const value = column.parse(entered);
    if (!Number.isFinite(value)) {
      // The row's data-* is the last thing that was actually saved, so it is what the field should
      // show rather than sending NaN for the server to reject.
      input.value = column.format(row.dataset[field]);
      this._status(`"${entered}" is not a ${column.noun}, so nothing changed.`);
      return;
    }

    // The endpoint writes a complete position, so the four untouched values come from the row.
    const edited = { time: parseFloat(row.dataset["time"]) };
    for (const name of ["x", "y", "width", "height"]) {
      edited[name] = parseFloat(row.dataset[name]);
    }
    edited[field] = value;

    this._commit(edited, {
      positionId: row.dataset["positionId"],
      // Sent even when only the geometry changed: leaving it out would mean "wherever the playhead
      // is", which is not what editing a numbered row asks for.
      time: edited.time,
    });
  }

  _onPanelInputKeyDown(event) {
    if (event.key !== "Enter" || !event.target.matches?.(POSITION_INPUT_SELECTOR)) return;
    // These fields sit inside the annotation form, where Enter would otherwise submit the whole
    // annotation - reconciling the points and reloading the detail form as a side effect of
    // committing one number.
    event.preventDefault();
    // Blurring is what fires `change`, so there is a single path into _onPanelInputChange. It is
    // also what really stops the implicit submission, which browsers key off the focused element.
    event.target.blur();
  }

  _onLocatorClick(event) {
    const locator = event.target.closest(".blur-position-locator");
    if (!locator) return;

    if (locator.closest(".track-item")?.classList.contains("active-track-item")) {
      event.stopPropagation();
      return;
    }

    // A different blur: let the click through so the item's handler selects it and loads its form,
    // but ask select() to land on the point that was clicked instead of the blur's start.
    const time = parseFloat(locator.dataset["positionTime"]);
    this.pendingPoint = Number.isFinite(time)
      ? { time, positionId: locator.dataset["positionId"] }
      : null;
  }

  // Drag a dot along the bar to retime its point. Only on the selected blur, because the panel rows
  // this reads for geometry belong to the current selection - and a dot on any other blur has to
  // keep behaving like a plain click, which is what selects that blur in the first place.
  _onLocatorPointerDown(event) {
    if (event.button !== 0) return;
    const locator = event.target.closest?.(".blur-position-locator");
    if (!locator) return;
    const item = locator.closest(".track-item");
    if (!item || item.dataset["annotationId"] !== this.annotationId) return;

    const positionId = locator.dataset["positionId"];
    const point = this._positions().find((position) => position.id === positionId);
    const bar = item.getBoundingClientRect();
    const itemStart = parseFloat(item.dataset["start"]);
    const itemEnd = parseFloat(item.dataset["end"]);
    if (!point || !(bar.width > 0) || !(itemEnd > itemStart)) return;

    // Part of keeping the track item's own HTML5 drag out of this gesture; see the dragstart
    // backstop in the constructor for the rest of it.
    event.preventDefault();

    this._goToPoint(positionId, point.time);

    // Only the point's time changes. Captured once, so a repaint partway through the drag cannot
    // substitute an interpolated rect for the point's real one.
    const rect = { x: point.x, y: point.y, width: point.width, height: point.height };
    const originalPositionTime = locator.dataset["positionTime"];
    let time = point.time;

    const timeAt = (pointerEvent) => {
      const fraction = (pointerEvent.clientX - bar.left) / bar.width;
      return Math.min(
        itemEnd,
        Math.max(itemStart, itemStart + fraction * (itemEnd - itemStart)),
      );
    };

    locator.setPointerCapture(event.pointerId);
    locator.classList.add("dragging-blur-position-locator");
    this.draggingLocator = true;
    if (!this.video.paused) this.video.pause();

    const cleanup = () => {
      // Listeners first, capture second: releasing capture fires lostpointercapture, and that is
      // wired to onCancel below, so a release with the listener still attached would cancel a
      // gesture that had already committed.
      locator.removeEventListener("pointermove", onMove);
      locator.removeEventListener("pointerup", onUp);
      locator.removeEventListener("pointercancel", onCancel);
      locator.removeEventListener("lostpointercapture", onCancel);
      if (locator.hasPointerCapture(event.pointerId)) {
        locator.releasePointerCapture(event.pointerId);
      }
      locator.classList.remove("dragging-blur-position-locator");
      this.draggingLocator = false;
      this.gesture = null;
    };

    const onMove = (moveEvent) => {
      time = timeAt(moveEvent);
      locator.dataset["positionTime"] = String(time);
      placeLocators(item);
      // Seeking is what makes this a retime the user can aim: they see the frame the point will land
      // on, with the point's own geometry over it rather than the interpolation _render would draw.
      this.video.currentTime = time;
      this._paint(rect);
    };

    const onUp = () => {
      const settled = Math.round(time * 100) / 100;
      cleanup();
      if (Math.abs(settled - point.time) <= SAME_TIME_SECONDS) {
        // A click, not a drag. Nothing to save; the click event that follows seeks to the point.
        locator.dataset["positionTime"] = originalPositionTime;
        placeLocators(item);
        this._render();
        return;
      }
      this._commit(rect, { positionId, time: settled });
    };

    const onCancel = () => {
      cleanup();
      locator.dataset["positionTime"] = originalPositionTime;
      placeLocators(item);
      this._render();
    };

    // Registered as a gesture so _render leaves the painted rect and the moving dot alone.
    this.gesture = { cancel: onCancel };
    locator.addEventListener("pointermove", onMove);
    locator.addEventListener("pointerup", onUp);
    locator.addEventListener("pointercancel", onCancel);
    // The dot is the capture target and it lives in the track item, which any save replaces
    // wholesale. Losing capture that way would strand the drag: pointerup would go elsewhere,
    // cleanup would never run, and `gesture` would stay set - freezing the rig for good.
    locator.addEventListener("lostpointercapture", onCancel);
  }

  _onLocatorKeyDown(event) {
    const locator = event.target.closest?.(".blur-position-locator");
    if (!locator) return;

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      this._goToPoint(locator.dataset["positionId"], locator.dataset["positionTime"]);
      return;
    }

    if (event.key !== "Delete" && event.key !== "Backspace") return;
    event.preventDefault();
    event.stopPropagation();
    // Deleting a point on an unselected blur would rebuild a panel belonging to some other
    // annotation, so this stays with the selection - the same rule as dragging a dot.
    if (locator.closest(".track-item")?.dataset["annotationId"] !== this.annotationId) return;
    this._delete(locator);
  }
}
