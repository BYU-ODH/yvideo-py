// The editor's outline around an annotation drawn on the video frame: the blur rig and the comment
// rig. Each editor decides where its box comes from and how a change is stored; this module owns
// building the outline and turning pointer drags and arrow keys into frame-percentage rects.

import { clampRect, edgesForHandle, percentWithin, resizeRect } from "./video-geometry.js";

export const NUDGE_PERCENT = 0.5;
export const NUDGE_PERCENT_LARGE = 5;
// Arrow keys arrive in bursts, so a request per keystroke would flood the endpoint and store every
// intermediate position as though the user had meant to stop there.
export const NUDGE_COMMIT_MS = 400;

// Clamping here rather than in each gesture means a pointer dragged past the edge of the window
// behaves like one held against the edge.
export function pointWithin(point, frame) {
  const percent = percentWithin({ x: point.x, y: point.y, width: 0, height: 0 }, frame);
  return {
    x: Math.min(100, Math.max(0, percent.x)),
    y: Math.min(100, Math.max(0, percent.y)),
  };
}

// Translating by the pointer's travel, rather than centering the box on the pointer, is what lets
// a user grab a corner of the box and keep the same grip on it.
export function movedRect(origin, startPoint, currentPoint) {
  return {
    x: origin.x + (currentPoint.x - startPoint.x),
    y: origin.y + (currentPoint.y - startPoint.y),
    width: origin.width,
    height: origin.height,
  };
}

const NUDGE_DIRECTIONS = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

/** The {dx, dy} an arrow key nudges by, or null for any other key. */
export function nudgeDelta({ key, shiftKey }) {
  const direction = NUDGE_DIRECTIONS[key];
  if (!direction) return null;
  const step = shiftKey ? NUDGE_PERCENT_LARGE : NUDGE_PERCENT;
  return { dx: direction[0] * step, dy: direction[1] * step };
}

/**
 * A painted-but-unwritten keyboard nudge, written once the keys stop.
 *
 * Held apart from a pointer gesture because the two end differently: a cancelled drag is
 * discarded, but a nudge the user can see is an edit they made, so it is flushed.
 */
export class PendingNudge {
  constructor(onFlush, delay = NUDGE_COMMIT_MS) {
    this.onFlush = onFlush;
    this.delay = delay;
    this.value = null;
    this.timer = null;
  }

  hold(value) {
    clearTimeout(this.timer);
    this.value = value;
    this.timer = setTimeout(() => this.flush(), this.delay);
  }

  flush() {
    const value = this.value;
    if (value === null) return;
    this.discard();
    this.onFlush(value);
  }

  /** Forget the nudge without writing it. Does not repaint - callers decide that. */
  discard() {
    clearTimeout(this.timer);
    this.value = null;
    this.timer = null;
  }
}

/** A focusable outline with a grip for each named handle. The caller attaches it to the frame. */
export function createRig({ id, label, title, keyShortcuts, handles, handleClass }) {
  const rig = document.createElement("div");
  rig.id = id;
  rig.className = "overlay-rig";
  rig.tabIndex = 0;
  rig.hidden = true;
  rig.setAttribute("role", "group");
  rig.setAttribute("aria-label", label);
  rig.setAttribute("aria-keyshortcuts", keyShortcuts);
  rig.title = title;
  for (const name of handles) {
    const handle = document.createElement("div");
    handle.className = `overlay-resize-handle ${handleClass}`;
    handle.dataset["handle"] = name;
    rig.appendChild(handle);
  }
  return rig;
}

/**
 * Follow a pointer from `event` (a pointerdown on a rig or one of its grips) until it is released.
 *
 * `onPaint` gets each clamped rect while the pointer moves, `onCommit` the final one on release,
 * and `onCancel` is called instead on pointercancel or Escape. Returns `{cancel}`, or null when the
 * frame has not been laid out and there is nothing to measure the pointer against.
 */
export function beginRectGesture(
  event,
  { frame, origin, handleName = null, limits, onPaint, onCommit, onCancel },
) {
  if (!(frame.width > 0) || !(frame.height > 0)) return null;
  let computeRect;
  if (handleName) {
    const edges = edgesForHandle(handleName);
    if (!edges) return null;
    const options = { ...edges, ...limits };
    computeRect = (_startPoint, currentPoint) => resizeRect(origin, currentPoint, options);
  } else {
    computeRect = (startPoint, currentPoint) => movedRect(origin, startPoint, currentPoint);
  }
  event.preventDefault();

  const target = event.currentTarget;
  target.setPointerCapture(event.pointerId);
  const pointOf = (pointerEvent) =>
    pointWithin({ x: pointerEvent.clientX, y: pointerEvent.clientY }, frame);
  const startPoint = pointOf(event);
  const rectAt = (pointerEvent) => clampRect(computeRect(startPoint, pointOf(pointerEvent)), limits);

  const cleanup = () => {
    if (target.hasPointerCapture(event.pointerId)) {
      target.releasePointerCapture(event.pointerId);
    }
    target.removeEventListener("pointermove", onMove);
    target.removeEventListener("pointerup", onUp);
    target.removeEventListener("pointercancel", cancel);
    document.removeEventListener("keydown", onKeyDown, true);
  };

  const onMove = (moveEvent) => onPaint(rectAt(moveEvent));

  const onUp = (upEvent) => {
    const rect = rectAt(upEvent);
    cleanup();
    onCommit(rect);
  };

  const cancel = () => {
    cleanup();
    onCancel();
  };

  const onKeyDown = (keyEvent) => {
    if (keyEvent.key !== "Escape") return;
    // The player seeks on bare keystrokes from a document-level listener.
    keyEvent.stopPropagation();
    keyEvent.preventDefault();
    cancel();
  };

  target.addEventListener("pointermove", onMove);
  target.addEventListener("pointerup", onUp);
  target.addEventListener("pointercancel", cancel);
  // Capture phase, so a focused rig's own Escape handling cannot keep the gesture alive.
  document.addEventListener("keydown", onKeyDown, true);
  return { cancel };
}
