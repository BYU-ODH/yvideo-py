// Pure geometry for the video frame and the annotations drawn on top of it.
//
// This module is deliberately free of DOM types: every function takes and returns plain
// numbers and {x, y, width, height} objects. That is what lets it be the single definition
// of "where is the video frame" and "where is a blur at time t" -- the player's renderer and
// the editor's drag handles both call these, so they cannot disagree about geometry -- and it
// is what makes those definitions testable without a browser (see tests/js/).
//
// Deciding *which* element to measure and *when* is DOM orchestration and belongs in
// AnnotationPlayer, not here.

// The rectangle a video's picture actually occupies inside its element box, mirroring
// `object-fit: contain` with the default centered `object-position`. Letterbox bars (or
// pillarbox bars) are the leftover space, split evenly.
//
// Returning the full element box when the intrinsic size is unusable -- audio-only media, a
// 0x0 poster, or metadata that has not loaded yet -- is a deliberate fallback rather than an
// error: the overlay degrades to covering the whole element, which is where it sat before
// this module existed, instead of collapsing to nothing or propagating NaN into styles.
export function contentRect(elemWidth, elemHeight, intrinsicWidth, intrinsicHeight) {
  // Coerce rather than pass through: a non-finite dimension would otherwise reach a style as
  // `NaNpx`, which CSS silently drops, leaving the overlay wherever it happened to be. A
  // zero-size rect is at least a predictable, visibly-wrong answer.
  const fullBox = {
    x: 0,
    y: 0,
    width: Number.isFinite(elemWidth) ? elemWidth : 0,
    height: Number.isFinite(elemHeight) ? elemHeight : 0,
  };
  if (
    !(elemWidth > 0) ||
    !(elemHeight > 0) ||
    !(intrinsicWidth > 0) ||
    !(intrinsicHeight > 0)
  ) {
    return fullBox;
  }

  const scale = Math.min(elemWidth / intrinsicWidth, elemHeight / intrinsicHeight);
  if (!Number.isFinite(scale)) {
    return fullBox;
  }

  const width = intrinsicWidth * scale;
  const height = intrinsicHeight * scale;
  return {
    x: (elemWidth - width) / 2,
    y: (elemHeight - height) / 2,
    width,
    height,
  };
}

// A rect in frame percentages, forced inside the frame and no smaller than the minimum.
//
// This deliberately mirrors BlurAnnotationPosition.save() in core/models.py, which is the
// authority -- the server clamps whatever it is sent. Applying the same rule while dragging is
// what stops the box from jumping on release: the user always sees the geometry that will be
// stored. Order matters, and matches the server's: size is settled first, then the origin is
// pulled in far enough to keep the whole box on screen.
export function clampRect(rect, { minWidth = 0, minHeight = 0 } = {}) {
  const width = Math.min(100, Math.max(minWidth, rect.width));
  const height = Math.min(100, Math.max(minHeight, rect.height));
  return {
    x: Math.min(100 - width, Math.max(0, rect.x)),
    y: Math.min(100 - height, Math.max(0, rect.y)),
    width,
    height,
  };
}

// A pixel rect expressed as percentages of `boxRect`, which is the coordinate space every
// stored annotation lives in. Both arguments are plain {x, y, width, height}, which a DOMRect
// already satisfies, so callers can pass getBoundingClientRect() results straight through.
//
// A zero-area reference box means the video has not been laid out yet. Returning zeros there is
// a deliberate floor: dividing would emit NaN, and a NaN in a style is dropped silently, which
// would leave a blur wherever it last happened to be rather than visibly wrong.
export function percentWithin(rect, boxRect) {
  if (!(boxRect.width > 0) || !(boxRect.height > 0)) {
    return { x: 0, y: 0, width: 0, height: 0 };
  }
  return {
    x: ((rect.x - boxRect.x) / boxRect.width) * 100,
    y: ((rect.y - boxRect.y) / boxRect.height) * 100,
    width: (rect.width / boxRect.width) * 100,
    height: (rect.height / boxRect.height) * 100,
  };
}

// A rect with one corner dragged to `point`, in the same percentage space. The corner opposite
// the dragged one stays put, which is what makes a corner handle feel anchored.
//
// The minimums stop the box from inverting as the pointer crosses the far edge -- without them a
// drag past the anchor produces a negative width, and a negative width in a style is dropped, so
// the box would jump to whatever size it had before while the pointer kept going.
export function resizeRect(
  origin,
  point,
  { movesLeft = false, movesTop = false, minWidth = 0, minHeight = 0 } = {},
) {
  const right = origin.x + origin.width;
  const bottom = origin.y + origin.height;
  const x = movesLeft ? Math.min(point.x, right - minWidth) : origin.x;
  const y = movesTop ? Math.min(point.y, bottom - minHeight) : origin.y;
  return {
    x,
    y,
    width: movesLeft ? right - x : Math.max(minWidth, point.x - x),
    height: movesTop ? bottom - y : Math.max(minHeight, point.y - y),
  };
}

function lerp(from, to, fraction) {
  return from + (to - from) * fraction;
}

// The rectangle a blur occupies at `time`, linearly interpolated between the two positions
// that bracket it. `positions` must be sorted ascending by `time` -- the server guarantees
// this (BlurAnnotation.to_player_json orders by time).
//
// Interpolation is what makes a handful of positions enough to keep a moving subject covered:
// with a step function the box lags behind the subject between positions and briefly exposes
// the very thing it exists to hide. Before the first position and after the last, the value
// is held constant rather than extrapolated, so a blur can never drift somewhere its author
// never put it.
export function rectAtTime(positions, time) {
  if (!positions || positions.length === 0) {
    return null;
  }

  const asRect = (position) => ({
    x: position.x,
    y: position.y,
    width: position.width,
    height: position.height,
  });

  const first = positions[0];
  if (positions.length === 1 || time <= first.time) {
    return asRect(first);
  }
  const last = positions[positions.length - 1];
  if (time >= last.time) {
    return asRect(last);
  }

  // Linear scan: a blur has a handful of positions, so this is cheaper than any index, and
  // stateless (no cursor to invalidate when the editor adds or moves a position mid-session).
  for (let i = 1; i < positions.length; i++) {
    const to = positions[i];
    if (to.time < time) continue;

    const from = positions[i - 1];
    const span = to.time - from.time;
    // Two positions sharing a timestamp shouldn't exist, but a divide-by-zero here would
    // put NaN into a style and drop the blur entirely, so snap to the later one.
    const fraction = span > 0 ? (time - from.time) / span : 1;
    return {
      x: lerp(from.x, to.x, fraction),
      y: lerp(from.y, to.y, fraction),
      width: lerp(from.width, to.width, fraction),
      height: lerp(from.height, to.height, fraction),
    };
  }

  return asRect(last);
}
