// Direct manipulation of blur regions on the video frame.
//
// The rig (`#blur-edit-rig`) is a separate element owned by this module, not the player's
// `.blur-position` div. That separation is what makes editing possible at all: the player's blur
// is `pointer-events: none` so it never intercepts a student's clicks, and nothing may re-enable
// events on it without putting playback at risk. It also means the player is free to destroy and
// rebuild its blur div whenever the playhead leaves the annotation's window - the rig simply
// reattaches (see _ensureRig).
//
// A blur always arrives with a box already on the frame (BlurAnnotation.ensure_first_position), so
// there is nothing here that creates one. Every gesture moves or resizes the box that is there.
// That is the whole metaphor, and it is deliberate: gestures that read as "add a blur" are what
// left users of the old system unable to work out how to get two blurs on screen at once, since
// the thing being added was really a point on one blur's path.
//
// One rule governs every gesture: a commit says "the blur belongs here, at the time I am looking
// at". It never names a position id. The server decides whether that is a new point or an
// existing one, because only the server knows where the stored points are. That is what turns
// "move the box at a new time" into an added point rather than a silent retime of whichever
// point happened to be selected, and it is required now that the rendered box is usually a tween
// between two points and so has no single owning row.

import { clampRect, percentWithin, rectAtTime, resizeRect } from "./video-geometry.js";
import { createElementFromHTMLString, getCSRFToken } from "./utils.js";

// Mirrors BLUR_MIN_WIDTH / BLUR_MIN_HEIGHT in core/models.py, which remains the authority - the
// server clamps whatever it is sent. Matching here is what keeps the box from jumping on release.
const MIN_WIDTH = 3;
const MIN_HEIGHT = 4;

// The eight handles, and which edges each one drags. Corners move one edge on each axis; the four
// midpoints move a single edge, so a box can be adjusted in one axis without disturbing the other.
const HANDLES = [
  ["nw", { movesLeft: true, movesTop: true }],
  ["n", { movesTop: true }],
  ["ne", { movesRight: true, movesTop: true }],
  ["e", { movesRight: true }],
  ["se", { movesRight: true, movesBottom: true }],
  ["s", { movesBottom: true }],
  ["sw", { movesLeft: true, movesBottom: true }],
  ["w", { movesLeft: true }],
];

// Mirrors BLUR_SNAP_SECONDS in core/models.py: a playhead within a frame or two of a stored point
// is editing *that point*, not the moment between points. The server applies the same rule when
// deciding whether a write lands on an existing point, and the two have to agree - see
// _pointAtPlayhead for what goes wrong when they do not.
const SNAP_SECONDS = 0.05;

const CLAMP_LIMITS = { minWidth: MIN_WIDTH, minHeight: MIN_HEIGHT };

// Arrow-key nudges, in percent of the frame. The small step is about a pixel or two on a desktop
// frame - fine enough to tuck an edge against a subject without a mouse.
const NUDGE_PERCENT = 0.5;
const NUDGE_PERCENT_LARGE = 5;
// How long the box has to sit still before a nudge is written. Arrow keys arrive in bursts, and a
// request per keystroke would both flood the endpoint and store every intermediate position as
// though the user had meant to stop there.
const NUDGE_COMMIT_MS = 400;
// `,` and `.` step the playhead by this much. The arrow keys the player uses for frame-stepping
// belong to the box once the rig has focus, so this is what replaces them.
const SCRUB_SECONDS = 0.1;
// Two ways to say "the same instant": times are stored to 2dp, so anything closer than half of
// that last digit is the same time expressed differently, not a change.
const SAME_TIME_SECONDS = 0.005;
// The panel's editable fields, and which value each one carries.
const POSITION_INPUTS = [
  ["position-time-input", "time"],
  ["position-x-input", "x"],
  ["position-y-input", "y"],
  ["position-width-input", "width"],
  ["position-height-input", "height"],
];
const POSITION_INPUT_SELECTOR = POSITION_INPUTS.map(([name]) => `.${name}`).join(", ");

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

// --- the two gestures, as pure functions of where the pointer went -----------

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
    // A keyboard nudge that has been painted but not written yet - see _nudge. Held separately
    // from `gesture` because the two end differently: a cancelled pointer drag is discarded, but a
    // nudge the user can see on screen is an edit they have made, so it is flushed.
    this.nudge = null;
    // True only while a timeline dot is being dragged, so the track item's own HTML5 drag can be
    // suppressed for the duration.
    this.draggingLocator = false;

    this._onRigPointerDown = this._onRigPointerDown.bind(this);
    this._onRigKeyDown = this._onRigKeyDown.bind(this);
    this._render = this._render.bind(this);

    // The rig tracks the playhead so it stays on top of the blur it is editing, which is also
    // what makes it show the interpolated geometry between two points.
    this.video.addEventListener("timeupdate", this._render);
    this.video.addEventListener("seeked", this._render);
    // A pending nudge carries the time it was made at, so a seek can safely write it out - and
    // doing so is what lets the rig go back to following the playhead instead of holding a rect
    // from a frame that is no longer on screen.
    this.video.addEventListener("seeking", () => this._flushNudge());

    // Delegated listeners, attached once. Every save replaces the panel rows and the track item,
    // so per-element listeners would need re-attaching each time and any miss would leave a dead
    // control behind.
    document.addEventListener("click", this._onPanelClick.bind(this));
    document.addEventListener("change", this._onPanelInputChange.bind(this));
    document.addEventListener("keydown", this._onPanelInputKeyDown.bind(this));
    if (this.timelineWrapper) {
      // Capture phase, deliberately. #timeline-wrapper is an *ancestor* of the track items, so a
      // bubbling listener here would run only after the item's own click handler had already
      // reloaded the detail form - and stopPropagation() at that point stops nothing.
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
      // A dot sits inside a `draggable="true"` track item, so if the native drag ever escapes the
      // dot gesture it moves the whole annotation along the timeline instead of retiming one point
      // - a far more destructive outcome than the one the user asked for. Taking the pointer
      // (setPointerCapture, plus preventDefault on pointerdown) is what suppresses it in practice;
      // this is the explicit backstop, since neither of those is specified to.
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
      // There has to be a box on screen to move. With the playhead outside the blur's window the
      // frame is empty and there is nothing to grab.
      this.video.currentTime = this.startTime;
    }
    this._render();
  }

  deselect() {
    // Flushed, not cancelled: a nudge is already visible on screen, so discarding it would throw
    // away an edit the user made. _commit captures the annotation id synchronously, so this is
    // still addressed to the blur being deselected.
    this._flushNudge();
    this.gesture?.cancel();
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

  // The item bar's dataset, not the form inputs. Both carry plain seconds, but the bar is what the
  // server just re-rendered - the form's fields are whatever the user has typed so far, which
  // during an edit is not yet the stored window.
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

  // The stored point `time` is sitting on, within the snap window, or null between points.
  //
  // This is what a gesture has to be measured against, and it is not the same thing as
  // _currentRect(). Parked 20ms past a point, the rig shows the interpolated rect for *that*
  // moment - a hair along the way toward the next point. Committing that rect while the server
  // snapped the write back onto the point stored it as the geometry at the point's own time,
  // which walked it a little toward its neighbour. The next drag then started from the moved
  // value and walked a little further, so the box visibly crept in width and height on every
  // release, in whichever direction the neighbouring point happened to lie.
  //
  // Takes a time rather than reading the playhead, because a keyboard nudge is written after a
  // delay and has to be filed under the moment it was made at, not the one the playhead reached.
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

  // Where a gesture starts from: the point being edited, if there is one, otherwise the tween the
  // rig is showing (which a commit will turn into a new point at the playhead).
  _gestureOrigin() {
    // A nudge that has not been written yet is still what the user can see, so it is what the next
    // gesture has to build on - otherwise grabbing the box after arrowing it would snap it back.
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
    for (const [name] of HANDLES) {
      const handle = document.createElement("div");
      handle.className = "blur-rig-handle";
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
    // There is a rect, so there is something to show. Stated here rather than left to _render
    // because a gesture can start with the rig hidden - dragging a dot seeks the playhead into the
    // blur's window - and a gesture that paints an invisible box is indistinguishable from a
    // broken one.
    rig.hidden = false;
    // Paint the player's blur too, so a drag previews the blurred result instead of an outline
    // that the blurred pixels lag behind. The player owns that element the rest of the time and
    // recomputes it from stored data on its next pass - which is also what silently undoes a
    // cancelled gesture, with no saved-state bookkeeping here.
    const blur = this.annotationBox.querySelector(`#blur-overlay-${this.annotationId}`);
    if (blur) applyRect(blur, rect);
  }

  _render() {
    if (!this.annotationId || this.gesture || this.nudge) return;
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
    this._markPositionActive(this._pointAt(this.video.currentTime)?.id);
  }

  // --- gestures --------------------------------------------------------------

  _onRigPointerDown(event) {
    if (!this.annotationId || event.button !== 0) return;
    const origin = this._gestureOrigin();
    if (!origin) return;

    const handle = event.target.closest(".blur-rig-handle");
    if (handle) {
      const edges = HANDLES.find(([name]) => name === handle.dataset["handle"])?.[1];
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

    // A moving picture cannot be aimed at, and the time a commit lands on is whatever the
    // playhead reads on release - so freeze it. Pausing also settles the one race in this file:
    // applyAnnotations only reschedules itself while playing, so a paused player will not fight
    // the geometry being painted below. Playback is deliberately not resumed afterwards; it
    // would immediately glide the box away from where the user just put it.
    if (!this.video.paused) this.video.pause();

    // Dropped rather than flushed: the gesture starts from the nudged rect (see _gestureOrigin)
    // and will write it, so writing it separately first would store an intermediate position and
    // spend a request doing it.
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

  // Move the box by `dx`/`dy` percent. Painted at once and written only after the keystrokes stop,
  // so holding a key glides the box instead of queueing one request and one stored point per
  // repeat - and so the intermediate positions never become data.
  _nudge(dx, dy) {
    // Outside the blur's window the rig is hidden and a draw is refused, so a nudge there would be
    // an edit to something the user cannot see - which the server would then clamp onto whichever
    // end of the window is nearest.
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
    // The time is captured now, not read at flush time. A nudge describes the frame on screen when
    // the key was pressed, and the playhead can move in the 400ms before it is written - by a
    // click on a panel row, by playback, by anything. Carrying the time is what keeps the geometry
    // and the moment it belongs to together.
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
        // Writes a nudge without waiting out the commit delay. There is nothing for Enter to do on
        // a box sitting still: a point exists wherever the box has been adjusted, so "commit the
        // current position" and "do nothing" are the same instruction.
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

    // Every key handled above is one the player also binds, from a listener on `document` that
    // only steps aside for inputs and textareas. Without this, arrowing the box would also seek
    // the video or change the volume underneath it.
    event.preventDefault();
    event.stopPropagation();
  }

  _scrub(delta) {
    // Flushed first, because _commit reads the playhead to decide which point a write lands on -
    // and the nudge belongs to the frame the user was looking at, not the one they are moving to.
    this._flushNudge();
    // Clamped to the blur's own window: scrubbing from the rig is for finding the next moment
    // *within* this blur, and leaving the window would just hide the thing being edited.
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

    // The moment this write is about: whatever the caller named, or the playhead for a gesture on
    // the frame.
    const requested =
      time !== undefined ? time : Math.round(this.video.currentTime * 100) / 100;
    // The point's own time when one is being edited, so client and server cannot disagree about
    // which moment this write belongs to. Sending the raw playhead and letting the server snap it
    // meant the geometry described one instant and was filed under another.
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
    // comparing against where it ended up. Derived rather than declared by the caller: what
    // actually changed is a better thing to report than what the caller believed it was doing.
    const previousTime =
      positionId === undefined
        ? editing?.time
        : this._positions().find((position) => position.id === String(positionId))?.time;

    let response;
    try {
      response = await fetch(`/annotations/blur/${annotationId}/positions/`, {
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
        response.status === 409
          ? "Another blur point is already at that time."
          : "That blur point could not be saved.",
      );
      return false;
    }

    const payload = await response.json();
    if (this._applySaved(annotationId, payload)) {
      const at = `${Number(payload["time"]).toFixed(2)}s`;
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

  async _delete(positionId) {
    const annotationId = this.annotationId;
    const response = await fetch(`/annotations/blur/positions/${positionId}/`, {
      method: "DELETE",
      headers: { "X-CSRFToken": getCSRFToken() },
    });
    if (!response.ok) {
      console.error(`Failed to delete blur point (${response.status})`);
      this._status(
        response.status === 409
          ? "The first point follows the blur's start time and cannot be deleted."
          : "That blur point could not be deleted.",
      );
      return false;
    }
    if (this._applySaved(annotationId, await response.json())) {
      this._status("Point deleted");
    }
    return true;
  }

  // A save that failed. Both halves are addressed to the blur the request was made for, because a
  // request can outlive its selection: reporting into whichever panel happens to be on screen would
  // put an error about one annotation in front of another, and repaint a blur nobody asked about.
  _reportFailure(annotationId, message) {
    if (annotationId !== this.annotationId) return;
    this._status(message);
    // Put the box back where the stored points say it is, rather than leaving the user looking at
    // geometry that was not saved.
    this._render();
  }

  /** @returns {boolean} whether the response was still describing the selected blur. */
  _applySaved(annotationId, payload) {
    // The user may have selected something else while the request was in flight, in which case
    // this response describes a panel that is no longer on screen.
    if (annotationId !== this.annotationId) return false;

    // Only the table, not the whole wrapper. The help text and the status line sit outside
    // #positions-list precisely so a save leaves them alone: replacing the status line would mean
    // re-inserting a live region with its message already inside it, which screen readers generally
    // do not announce. The full-wrapper path stays as the fallback for a payload whose shape this
    // does not recognise.
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
    return true;
  }

  // --- panel and timeline ----------------------------------------------------

  // What just happened, for the panel's live region. Written from here rather than rendered by the
  // server because it reports an action, not the state of the data - and the rows next to it are
  // already the record of the state.
  _status(message) {
    const status = document.getElementById("blur-position-status");
    if (status) status.textContent = message;
  }

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

  // The panel's time/x/y/W/H fields. These name a point by id, so a numeric edit lands on that
  // exact row wherever the playhead happens to be - the mirror image of a gesture on the frame,
  // which names a moment and lets the server work out which row that is.
  _onPanelInputChange(event) {
    const input = event.target;
    if (!this.annotationId || !input.matches?.(POSITION_INPUT_SELECTOR)) return;
    const row = input.closest(".position-entry");
    if (!row) return;

    const field = POSITION_INPUTS.find(([name]) => input.classList.contains(name))?.[1];
    if (!field) return;
    const entered = input.value;
    const value = parseFloat(entered);
    if (!Number.isFinite(value)) {
      // Put the stored value back rather than sending NaN for the server to reject: the row's
      // data-* is the last thing that was actually saved, so it is what the field should show.
      input.value = row.dataset[field];
      this._status(`"${entered}" is not a number, so nothing changed.`);
      return;
    }

    // The whole row, with the one edited value substituted: the endpoint writes a complete
    // position, so the other four have to come from somewhere, and the row is the client's record
    // of what was last saved.
    const edited = { time: parseFloat(row.dataset["time"]) };
    for (const name of ["x", "y", "width", "height"]) {
      edited[name] = parseFloat(row.dataset[name]);
    }
    edited[field] = value;

    this._commit(edited, {
      positionId: row.dataset["positionId"],
      // A time is always sent, even when only the geometry changed: leaving it out would mean
      // "wherever the playhead is", which is not what editing a numbered row asks for.
      time: edited.time,
    });
  }

  _onPanelInputKeyDown(event) {
    if (event.key !== "Enter" || !event.target.matches?.(POSITION_INPUT_SELECTOR)) return;
    // These fields sit inside the annotation form, where Enter would otherwise submit the whole
    // annotation - reconciling the points and reloading the detail form as a side effect of
    // committing one number.
    event.preventDefault();
    // Blurring is what fires `change`, so there is one path into _onPanelInputChange rather than
    // two that could disagree about what a committed edit means. It also happens to be what really
    // stops the implicit submission above, since browsers key that off the focused element.
    event.target.blur();
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

  // Drag a dot along the bar to retime its point. Only on the selected blur, because the panel
  // rows this reads for geometry and the annotation a write is addressed to both belong to the
  // current selection - and a dot on any other blur has to keep behaving like a plain click, which
  // is what selects that blur in the first place.
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

    // The point keeps its geometry; only its time changes. Captured once, so a repaint partway
    // through the drag cannot substitute an interpolated rect for the point's real one.
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
      // Retimed in place and re-placed through placeLocators, so the dot follows the pointer by
      // the same arithmetic that will position it once this is saved.
      locator.dataset["positionTime"] = String(time);
      placeLocators(item);
      // Seeking is what makes this a retime the user can aim: they see the frame the point will
      // land on, with the point's own geometry over it - which is exactly what will show there
      // afterwards, and not the interpolation that _render would otherwise draw.
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
    // wholesale. Losing capture that way would strand the drag: pointerup would go somewhere else,
    // cleanup would never run, and `gesture` would stay set - freezing the rig for good.
    locator.addEventListener("lostpointercapture", onCancel);
  }

  _onLocatorKeyDown(event) {
    const locator = event.target.closest?.(".blur-position-locator");
    if (!locator) return;

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      const time = parseFloat(locator.dataset["positionTime"]);
      if (Number.isFinite(time)) this.video.currentTime = time;
      this._markPositionActive(locator.dataset["positionId"]);
      return;
    }

    if (event.key !== "Delete" && event.key !== "Backspace") return;
    event.preventDefault();
    event.stopPropagation();
    // Deleting a point on an unselected blur would rebuild a panel belonging to some other
    // annotation, so this stays with the selection - the same rule as dragging a dot.
    if (locator.closest(".track-item")?.dataset["annotationId"] !== this.annotationId) return;
    this._delete(locator.dataset["positionId"]);
  }
}
