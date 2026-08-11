// Run with:  node --test tests/js/
//
// These cover the only arithmetic in the blur feature. They need no browser, so they can afford to
// be exhaustive about the edge cases that would otherwise surface as a misplaced blur.
import assert from "node:assert/strict";
import test from "node:test";

import {
  RESIZE_HANDLES,
  clampRect,
  contentRect,
  edgesForHandle,
  percentWithin,
  rectAtTime,
  resizeRect,
} from "../../core/static/js/video-geometry.js";

test("contentRect pillarboxes a 16:9 video in a wider box", () => {
  // 1000x400 box, 16:9 source: height is the binding dimension.
  const rect = contentRect(1000, 400, 1280, 720);
  assert.equal(rect.height, 400);
  assert.equal(rect.width, 400 * (1280 / 720));
  assert.equal(rect.y, 0, "no letterboxing on the binding axis");
  assert.equal(rect.x, (1000 - rect.width) / 2, "bars split evenly");
});

test("contentRect letterboxes a 16:9 video in a taller box", () => {
  // 640x1000 box, 16:9 source: width is the binding dimension.
  const rect = contentRect(640, 1000, 1280, 720);
  assert.equal(rect.width, 640);
  assert.equal(rect.height, 640 * (720 / 1280));
  assert.equal(rect.x, 0);
  assert.equal(rect.y, (1000 - rect.height) / 2);
});

test("contentRect fills the box exactly when the ratios match", () => {
  const rect = contentRect(1920, 1080, 1280, 720);
  assert.deepEqual(rect, { x: 0, y: 0, width: 1920, height: 1080 });
});

test("contentRect letterboxes a portrait source in a landscape box", () => {
  // The branch that the seeded 16:9 demo media can never reach on its own.
  const rect = contentRect(1600, 900, 1080, 1920);
  assert.equal(rect.height, 900);
  assert.equal(rect.width, 900 * (1080 / 1920));
  assert.ok(rect.x > 0, "pillarboxed");
  assert.equal(rect.y, 0);
});

test("contentRect preserves the source aspect ratio at aspect extremes", () => {
  // The property that matters most: the picture is never distorted.
  for (const [boxWidth, boxHeight] of [
    [1600, 600],
    [300, 1600],
    [2000, 1125],
    [17, 993],
  ]) {
    const rect = contentRect(boxWidth, boxHeight, 1280, 720);
    assert.ok(
      Math.abs(rect.width / rect.height - 1280 / 720) < 1e-9,
      `distorted at ${boxWidth}x${boxHeight}`,
    );
    assert.ok(rect.width <= boxWidth + 1e-9, "does not overflow horizontally");
    assert.ok(rect.height <= boxHeight + 1e-9, "does not overflow vertically");
  }
});

test("contentRect falls back to the full element box when intrinsic size is unusable", () => {
  // Audio-only media, and metadata that has not loaded yet: videoWidth/videoHeight are 0.
  const fullBox = { x: 0, y: 0, width: 800, height: 450 };
  assert.deepEqual(contentRect(800, 450, 0, 0), fullBox);
  assert.deepEqual(contentRect(800, 450, 1280, 0), fullBox);
  assert.deepEqual(contentRect(800, 450, undefined, undefined), fullBox);
  assert.deepEqual(contentRect(800, 450, NaN, NaN), fullBox);
});

test("contentRect never emits NaN for a degenerate element box", () => {
  for (const [w, h] of [
    [0, 0],
    [NaN, NaN],
    [800, 0],
  ]) {
    const rect = contentRect(w, h, 1280, 720);
    for (const [key, value] of Object.entries(rect)) {
      assert.ok(!Number.isNaN(value), `${key} is NaN for box ${w}x${h}`);
    }
  }
});

// --- rectAtTime -------------------------------------------------------------

// Deliberately asymmetric on every axis so that swapping x/y or width/height, or
// interpolating one field with another's endpoints, cannot cancel out and pass.
const POSITIONS = [
  { time: 2, x: 10, y: 20, width: 30, height: 40 },
  { time: 6, x: 50, y: 24, width: 22, height: 48 },
];

test("rectAtTime returns null when there are no positions", () => {
  assert.equal(rectAtTime([], 1), null);
  assert.equal(rectAtTime(undefined, 1), null);
});

test("rectAtTime holds a lone position constant at any time", () => {
  const only = [{ time: 5, x: 1, y: 2, width: 3, height: 4 }];
  const expected = { x: 1, y: 2, width: 3, height: 4 };
  for (const time of [-100, 0, 5, 1000]) {
    assert.deepEqual(rectAtTime(only, time), expected);
  }
});

test("rectAtTime interpolates every field independently at the midpoint", () => {
  assert.deepEqual(rectAtTime(POSITIONS, 4), {
    x: 30,
    y: 22,
    width: 26,
    height: 44,
  });
});

test("rectAtTime interpolates off-center", () => {
  // A quarter of the way from t=2 to t=6.
  assert.deepEqual(rectAtTime(POSITIONS, 3), {
    x: 20,
    y: 21,
    width: 28,
    height: 42,
  });
});

test("rectAtTime returns the exact endpoints at the endpoints", () => {
  assert.deepEqual(rectAtTime(POSITIONS, 2), { x: 10, y: 20, width: 30, height: 40 });
  assert.deepEqual(rectAtTime(POSITIONS, 6), { x: 50, y: 24, width: 22, height: 48 });
});

test("rectAtTime holds constant outside the first and last position", () => {
  // Never extrapolate: a blur must not drift somewhere its author never put it.
  assert.deepEqual(rectAtTime(POSITIONS, -5), { x: 10, y: 20, width: 30, height: 40 });
  assert.deepEqual(rectAtTime(POSITIONS, 1.99), { x: 10, y: 20, width: 30, height: 40 });
  assert.deepEqual(rectAtTime(POSITIONS, 6.01), { x: 50, y: 24, width: 22, height: 48 });
  assert.deepEqual(rectAtTime(POSITIONS, 9999), { x: 50, y: 24, width: 22, height: 48 });
});

test("rectAtTime walks past intermediate positions to the right bracket", () => {
  const three = [
    { time: 0, x: 0, y: 0, width: 10, height: 10 },
    { time: 10, x: 100, y: 50, width: 10, height: 10 },
    { time: 20, x: 0, y: 0, width: 20, height: 30 },
  ];
  assert.deepEqual(rectAtTime(three, 15), { x: 50, y: 25, width: 15, height: 20 });
});

test("rectAtTime survives duplicate timestamps without emitting NaN", () => {
  const duplicated = [
    { time: 3, x: 0, y: 0, width: 10, height: 10 },
    { time: 3, x: 80, y: 60, width: 20, height: 30 },
  ];
  // Snaps to the later position rather than dividing by a zero-width span.
  assert.deepEqual(rectAtTime(duplicated, 3), { x: 0, y: 0, width: 10, height: 10 });
  const between = rectAtTime(duplicated, 3.000001);
  for (const [key, value] of Object.entries(between)) {
    assert.ok(!Number.isNaN(value), `${key} is NaN across a zero-width span`);
  }
});

test("rectAtTime does not mutate its input", () => {
  const snapshot = structuredClone(POSITIONS);
  rectAtTime(POSITIONS, 4);
  assert.deepEqual(POSITIONS, snapshot);
});

// --- clampRect -------------------------------------------------------------
//
// These mirror BlurAnnotationPosition.save(); core/tests/test_blur_positions.py asserts the
// same cases against the server. Any divergence shows up as a box that jumps on release.

test("clampRect leaves a rect that is already inside the frame alone", () => {
  const rect = { x: 12.5, y: 30, width: 22, height: 14 };
  assert.deepEqual(clampRect(rect, { minWidth: 3, minHeight: 4 }), rect);
});

test("clampRect pulls a rect back inside the frame instead of shrinking it", () => {
  // Dragging past the right/bottom edge must slide the box, not squash it: the user is
  // covering something of a fixed size and silently narrowing the box would expose it.
  const rect = clampRect({ x: 95, y: 92, width: 22, height: 14 });
  assert.deepEqual(rect, { x: 78, y: 86, width: 22, height: 14 });
});

test("clampRect clamps a negative origin to the top-left edge", () => {
  assert.deepEqual(clampRect({ x: -20, y: -5, width: 30, height: 10 }), {
    x: 0,
    y: 0,
    width: 30,
    height: 10,
  });
});

test("clampRect enforces the minimum size and caps at the full frame", () => {
  const tiny = clampRect({ x: 50, y: 50, width: 0.4, height: 0 }, { minWidth: 3, minHeight: 4 });
  assert.equal(tiny.width, 3);
  assert.equal(tiny.height, 4);

  const huge = clampRect({ x: 10, y: 10, width: 250, height: 180 });
  assert.deepEqual(huge, { x: 0, y: 0, width: 100, height: 100 });
});

test("clampRect does not mutate its input", () => {
  const rect = { x: 95, y: 92, width: 22, height: 14 };
  clampRect(rect);
  assert.deepEqual(rect, { x: 95, y: 92, width: 22, height: 14 });
});

// --- percentWithin ---------------------------------------------------------

test("percentWithin converts a pixel rect into frame percentages", () => {
  // A letterboxed frame: the reference box does not start at the viewport origin.
  const frame = { x: 40, y: 100, width: 800, height: 450 };
  assert.deepEqual(percentWithin({ x: 140, y: 145, width: 200, height: 90 }, frame), {
    x: 12.5,
    y: 10,
    width: 25,
    height: 20,
  });
});

test("percentWithin round-trips through the frame origin", () => {
  const frame = { x: 227.61, y: 0, width: 1444.78, height: 812.69 };
  const original = { x: 12.5, y: 30, width: 22, height: 14 };
  const pixels = {
    x: frame.x + (original.x / 100) * frame.width,
    y: frame.y + (original.y / 100) * frame.height,
    width: (original.width / 100) * frame.width,
    height: (original.height / 100) * frame.height,
  };
  const roundTripped = percentWithin(pixels, frame);
  for (const key of Object.keys(original)) {
    assert.ok(
      Math.abs(roundTripped[key] - original[key]) < 1e-9,
      `${key} drifted: ${roundTripped[key]} vs ${original[key]}`,
    );
  }
});

test("percentWithin returns zeros rather than NaN for an unlaid-out box", () => {
  assert.deepEqual(percentWithin({ x: 5, y: 5, width: 10, height: 10 }, { x: 0, y: 0, width: 0, height: 0 }), {
    x: 0,
    y: 0,
    width: 0,
    height: 0,
  });
});

// --- resizeRect ------------------------------------------------------------
//
// Shared by the blur rig and the comment box, which is the point: one definition of what dragging
// an edge means, so the two cannot drift apart. Each edge is named independently, so a corner
// handle and an edge handle are the same call with different flags.

const ORIGIN = { x: 20, y: 30, width: 40, height: 20 };
// The rig's eight handles, as BlurEditor's HANDLES declares them.
const CORNER_SE = { movesRight: true, movesBottom: true };
const CORNER_NW = { movesLeft: true, movesTop: true };
const CORNER_NE = { movesRight: true, movesTop: true };
const CORNER_SW = { movesLeft: true, movesBottom: true };

test("resizeRect drags the bottom-right corner and leaves the origin alone", () => {
  const resized = resizeRect(ORIGIN, { x: 80, y: 70 }, CORNER_SE);
  assert.deepEqual(resized, { x: 20, y: 30, width: 60, height: 40 });
});

test("resizeRect drags the top-left corner and holds the far corner still", () => {
  const resized = resizeRect(ORIGIN, { x: 10, y: 25 }, CORNER_NW);
  assert.deepEqual(resized, { x: 10, y: 25, width: 50, height: 25 });
  assert.equal(resized.x + resized.width, ORIGIN.x + ORIGIN.width, "right edge moved");
  assert.equal(resized.y + resized.height, ORIGIN.y + ORIGIN.height, "bottom edge moved");
});

test("resizeRect anchors the opposite edge for the other two corners", () => {
  const topRight = resizeRect(ORIGIN, { x: 75, y: 35 }, CORNER_NE);
  assert.equal(topRight.x, ORIGIN.x, "left edge should be anchored");
  assert.deepEqual(topRight, { x: 20, y: 35, width: 55, height: 15 });

  const bottomLeft = resizeRect(ORIGIN, { x: 30, y: 60 }, CORNER_SW);
  assert.equal(bottomLeft.y, ORIGIN.y, "top edge should be anchored");
  assert.deepEqual(bottomLeft, { x: 30, y: 30, width: 30, height: 30 });
});

// The four edge handles: the case an "else it must be the opposite edge" model cannot express, since
// deriving movesRight from !movesLeft makes every handle a corner.
test("resizeRect leaves the vertical axis alone for the left and right edges", () => {
  const east = resizeRect(ORIGIN, { x: 75, y: 999 }, { movesRight: true });
  assert.deepEqual(east, { x: 20, y: 30, width: 55, height: 20 });

  const west = resizeRect(ORIGIN, { x: 10, y: -999 }, { movesLeft: true });
  assert.deepEqual(west, { x: 10, y: 30, width: 50, height: 20 });
});

test("resizeRect leaves the horizontal axis alone for the top and bottom edges", () => {
  const south = resizeRect(ORIGIN, { x: 999, y: 70 }, { movesBottom: true });
  assert.deepEqual(south, { x: 20, y: 30, width: 40, height: 40 });

  const north = resizeRect(ORIGIN, { x: -999, y: 25 }, { movesTop: true });
  assert.deepEqual(north, { x: 20, y: 25, width: 40, height: 25 });
});

test("resizeRect with no edges named is the identity", () => {
  assert.deepEqual(resizeRect(ORIGIN, { x: 99, y: 99 }), { ...ORIGIN });
});

test("resizeRect refuses to invert when the pointer crosses the anchored edge", () => {
  // A negative width in a style is dropped, so the box would freeze at its old size while the
  // pointer kept going.
  const limits = { minWidth: 3, minHeight: 4 };
  const pastLeft = resizeRect(ORIGIN, { x: 95, y: 95 }, { ...CORNER_NW, ...limits });
  assert.equal(pastLeft.width, 3);
  assert.equal(pastLeft.height, 4);
  assert.equal(pastLeft.x, 57, "pinned just short of the anchored right edge");
  assert.equal(pastLeft.y, 46);

  const pastRight = resizeRect(ORIGIN, { x: 5, y: 5 }, { ...CORNER_SE, ...limits });
  assert.deepEqual(pastRight, { x: 20, y: 30, width: 3, height: 4 });

  // And an edge handle collapses only its own axis.
  const pastTop = resizeRect(ORIGIN, { x: 40, y: 95 }, { movesTop: true, ...limits });
  assert.deepEqual(pastTop, { x: 20, y: 46, width: 40, height: 4 });
});

test("resizeRect does not mutate its input", () => {
  const origin = { ...ORIGIN };
  resizeRect(origin, { x: 5, y: 5 }, CORNER_NW);
  assert.deepEqual(origin, ORIGIN);
});

// The handle table is shared by every resizable overlay - the blur rig and the comment box both
// build their grips and resize from it - so a gap here is a gap in both at once.
test("RESIZE_HANDLES names all eight grips of a box", () => {
  assert.deepEqual(
    RESIZE_HANDLES.map(([name]) => name),
    ["nw", "n", "ne", "e", "se", "s", "sw", "w"],
  );
});

test("each handle moves exactly the edges its compass name points at", () => {
  for (const [name, edges] of RESIZE_HANDLES) {
    assert.equal(Boolean(edges.movesTop), name.startsWith("n"), name);
    assert.equal(Boolean(edges.movesBottom), name.startsWith("s"), name);
    assert.equal(Boolean(edges.movesLeft), name.endsWith("w"), name);
    assert.equal(Boolean(edges.movesRight), name.endsWith("e"), name);
  }
});

test("corner handles move two edges and edge handles exactly one", () => {
  for (const [name, edges] of RESIZE_HANDLES) {
    const moved = Object.values(edges).filter(Boolean).length;
    assert.equal(moved, name.length, `${name} should move ${name.length} edge(s)`);
  }
});

test("edgesForHandle finds a handle by name and rejects anything else", () => {
  assert.deepEqual(edgesForHandle("se"), { movesRight: true, movesBottom: true });
  assert.equal(edgesForHandle("middle"), undefined);
  assert.equal(edgesForHandle(undefined), undefined);
});

test("an edge handle leaves the other axis untouched", () => {
  const origin = { x: 10, y: 20, width: 30, height: 40 };
  const pointer = { x: 75, y: 95 };

  const widened = resizeRect(origin, pointer, edgesForHandle("e"));
  assert.equal(widened.y, origin.y);
  assert.equal(widened.height, origin.height);
  assert.equal(widened.x, origin.x);
  assert.equal(widened.width, pointer.x - origin.x);

  // The same pointer through the corner below it moves both axes, which is the difference the
  // eight-handle table exists to express.
  const corner = resizeRect(origin, pointer, edgesForHandle("se"));
  assert.equal(corner.width, widened.width);
  assert.equal(corner.height, pointer.y - origin.y);
});
