// Run with:  node --test tests/js/
//
// These cover the only arithmetic in the blur feature. They need no browser, so they run in
// milliseconds and can afford to be exhaustive about the edge cases that would otherwise
// surface as a misplaced blur over copyrighted or explicit content.
import assert from "node:assert/strict";
import test from "node:test";

import { contentRect, rectAtTime } from "../../core/static/js/video-geometry.js";

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
  // The property the whole redesign exists to guarantee: the picture is never distorted.
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
