// Run with:  node --test tests/js/
import assert from "node:assert/strict";
import test from "node:test";

import {
  NUDGE_COMMIT_MS,
  NUDGE_PERCENT,
  NUDGE_PERCENT_LARGE,
  PendingNudge,
  movedRect,
  nudgeDelta,
  pointWithin,
} from "../../core/static/js/overlay-rig.js";

const FRAME = { x: 100, y: 50, width: 400, height: 200 };

test("pointWithin converts client pixels into frame percentages", () => {
  assert.deepEqual(pointWithin({ x: 200, y: 100 }, FRAME), { x: 25, y: 25 });
});

test("pointWithin holds a pointer past the frame against its edge", () => {
  assert.deepEqual(pointWithin({ x: 0, y: 1000 }, FRAME), { x: 0, y: 100 });
});

test("movedRect moves the box by the pointer's travel and keeps its size", () => {
  const origin = { x: 10, y: 20, width: 30, height: 40 };
  assert.deepEqual(movedRect(origin, { x: 50, y: 50 }, { x: 55, y: 45 }), {
    x: 15,
    y: 15,
    width: 30,
    height: 40,
  });
});

// The pointer's travel from where it went down, not its step from the last move, is what keeps the
// grip: a box held against an edge comes off it only once the pointer is back where it grabbed.
test("movedRect returns the box to its origin when the pointer returns", () => {
  const origin = { x: 80, y: 0, width: 20, height: 10 };
  assert.deepEqual(movedRect(origin, { x: 90, y: 5 }, { x: 90, y: 5 }), origin);
});

test("nudgeDelta steps each arrow key, and further with Shift", () => {
  assert.deepEqual(nudgeDelta({ key: "ArrowLeft" }), { dx: -NUDGE_PERCENT, dy: 0 });
  assert.deepEqual(nudgeDelta({ key: "ArrowRight" }), { dx: NUDGE_PERCENT, dy: 0 });
  assert.deepEqual(nudgeDelta({ key: "ArrowUp" }), { dx: 0, dy: -NUDGE_PERCENT });
  assert.deepEqual(nudgeDelta({ key: "ArrowDown", shiftKey: true }), {
    dx: 0,
    dy: NUDGE_PERCENT_LARGE,
  });
});

test("nudgeDelta ignores other keys", () => {
  assert.equal(nudgeDelta({ key: "Enter" }), null);
});

test("PendingNudge writes only the last value, once the keys stop", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const written = [];
  const nudge = new PendingNudge((value) => written.push(value));

  nudge.hold(1);
  t.mock.timers.tick(NUDGE_COMMIT_MS - 1);
  nudge.hold(2);
  t.mock.timers.tick(NUDGE_COMMIT_MS - 1);
  assert.deepEqual(written, []);

  t.mock.timers.tick(1);
  assert.deepEqual(written, [2]);
  assert.equal(nudge.value, null);
});

test("PendingNudge.flush writes at once, and discard drops the value unwritten", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const written = [];
  const nudge = new PendingNudge((value) => written.push(value));

  nudge.hold("flushed");
  nudge.flush();
  nudge.hold("discarded");
  nudge.discard();
  t.mock.timers.tick(NUDGE_COMMIT_MS);
  nudge.flush();

  assert.deepEqual(written, ["flushed"]);
});
