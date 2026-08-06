// Pure geometry for the video frame and the annotations drawn on top of it.
//
// Free of DOM types: every function takes and returns plain numbers and {x, y, width, height}
// objects, so the player's renderer and the editor's drag handles share one definition of "where is
// the video frame" and "where is a blur at time t", and both are testable without a browser (see
// tests/js/). Deciding *which* element to measure and *when* belongs in AnnotationPlayer.

// The rectangle a video's picture actually occupies inside its element box, mirroring
// `object-fit: contain` with the default centered `object-position`. Letterbox (or pillarbox) bars
// are the leftover space, split evenly.
//
// An unusable intrinsic size -- audio-only media, a 0x0 poster, metadata that has not loaded yet --
// degrades to the full element box rather than erroring. A NaN in a style is dropped silently, which
// would leave the overlay wherever it last happened to be; a zero-size rect is at least visibly
// wrong.
export function contentRect(elemWidth, elemHeight, intrinsicWidth, intrinsicHeight) {
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

// Mirrors BlurAnnotationPosition.save() in core/models.py, so a drag shows the geometry that will
// actually be stored and the box does not jump on release. Order matters and matches the server's:
// size is settled first, then the origin is pulled in far enough to keep the whole box on screen.
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

// Both arguments are plain {x, y, width, height}, which a DOMRect already satisfies, so callers can
// pass getBoundingClientRect() results straight through. A zero-area reference box means the video
// has not been laid out yet; zeros are the floor because dividing would emit NaN.
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

// All four edges are named independently, so a corner handle (two edges) and an edge handle (one)
// share the same arithmetic and an edge handle can leave one axis completely alone.
//
// The minimums stop the box from inverting as the pointer crosses the far edge: a negative width in
// a style is dropped, so the box would stick at its previous size while the pointer kept going.
export function resizeRect(
  origin,
  point,
  {
    movesLeft = false,
    movesRight = false,
    movesTop = false,
    movesBottom = false,
    minWidth = 0,
    minHeight = 0,
  } = {},
) {
  const right = origin.x + origin.width;
  const bottom = origin.y + origin.height;
  const x = movesLeft ? Math.min(point.x, right - minWidth) : origin.x;
  const y = movesTop ? Math.min(point.y, bottom - minHeight) : origin.y;
  return {
    x,
    y,
    width: movesLeft
      ? right - x
      : movesRight
        ? Math.max(minWidth, point.x - x)
        : origin.width,
    height: movesTop
      ? bottom - y
      : movesBottom
        ? Math.max(minHeight, point.y - y)
        : origin.height,
  };
}

function lerp(from, to, fraction) {
  return from + (to - from) * fraction;
}

// The rectangle a blur occupies at `time`, linearly interpolated between the two positions that
// bracket it. `positions` must be sorted ascending by `time` -- the server guarantees this
// (BlurAnnotation.to_player_json orders by time).
//
// Interpolation is what makes a handful of positions enough to keep a moving subject covered: with a
// step function the box lags behind between positions and briefly exposes the thing it exists to
// hide. Outside the first and last positions the value is held constant rather than extrapolated, so
// a blur can never drift somewhere its author never put it.
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

  // Linear scan: a blur has a handful of positions, so this is cheaper than any index, and stateless
  // (no cursor to invalidate when the editor adds or moves a position mid-session).
  for (let i = 1; i < positions.length; i++) {
    const to = positions[i];
    if (to.time < time) continue;

    const from = positions[i - 1];
    const span = to.time - from.time;
    // Two positions sharing a timestamp shouldn't exist, but a divide-by-zero here would put NaN
    // into a style and drop the blur entirely, so snap to the later one.
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

// Mirrors BLUR_TIME_PRECISION in core/models.py: BaseAnnotation.save() stores start_time and
// end_time to this many decimals, so a client-side prediction about what the server will do has
// to compare at the same precision.
const TIME_PRECISION = 2;
// Mirrors BLUR_RETIME_TOLERANCE_SECONDS in core/models.py. See there for why it is looser than
// the stored precision.
const RETIME_TOLERANCE_SECONDS = 0.02;

function roundToStoredTime(seconds) {
  const scale = 10 ** TIME_PRECISION;
  return Math.round(seconds * scale) / scale;
}

// How many of a blur's points a new time range would discard, given the times of its current points.
// The editor calls this to decide whether to warn before a retiming save: a blur covers content that
// must not be seen, so silently losing a point leaves a blur gliding somewhere it was never aimed.
//
// This predicts what BlurAnnotation.reconcile_positions will do - the same decision written twice, in
// two languages, where drift means either a warning about nothing or an unannounced deletion. Kept
// pure and DOM-free so tests/js/blur-retiming.test.js and the RetimingPointLossTests case table in
// core/tests/test_blur_positions.py can hold both implementations to the same answers.
export function pointsLostByRetiming(times, oldStart, oldEnd, newStart, newEnd) {
  if (!Number.isFinite(oldStart) || !Number.isFinite(oldEnd)) return 0;
  // Rounded to stored precision first: dragging the left handle computes the right edge as
  // left+width, which lands a hair under a whole number, so a raw 10.99999 against a point at 11.0
  // would warn about a loss the server, holding 11.00, is never going to inflict.
  const start = roundToStoredTime(newStart);
  const end = roundToStoredTime(newEnd);
  // Both halves of reconcile_positions' move test: the same tolerance *and* a start that actually
  // moved. Left float-exact, matching the server rather than defended against, because a duration
  // change near the tolerance has to fall the same side of it in both places.
  const durationChange = Math.abs(end - start - (oldEnd - oldStart));
  const isMove = durationChange <= RETIME_TOLERANCE_SECONDS && start !== oldStart;

  // A move carries the whole motion path with it, but the window pruning below still applies, so a
  // move is not automatically lossless: a start-only nudge of a hundredth reads as a move under the
  // tolerance and shifts every point, pushing the last one past an end that did not move.
  const delta = isMove ? start - oldStart : 0;
  const points = (times || [])
    .filter(Number.isFinite)
    .map((time) => (isMove ? roundToStoredTime(time + delta) : time));

  const leading = points.filter((time) => time < start).length;
  const trailing = points.filter((time) => time > end).length;
  // Of the points before the window, the latest normally survives: ensure_first_position re-pins it
  // to start_time rather than deleting it. The exception is a point already sitting exactly on the
  // new start, which supplies the geometry there and makes the survivor redundant.
  //
  // Exact equality is safe: a stored time is already at TIME_PRECISION and `start` has just been
  // rounded to it, so two times that should match are both k/100 and the identical double.
  const survivors = points.some((time) => time === start) ? 0 : Math.min(1, leading);
  return trailing + leading - survivors;
}
