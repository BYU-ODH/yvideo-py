// Run with:  node --test tests/js/
//
// pointsLostByRetiming predicts what BlurAnnotation.reconcile_positions is about to delete, so the
// editor can warn first. A wrong prediction is either a warning about nothing or a blur point
// deleted without being announced.
//
// CASES below is the contract, and is the same table RetimingPointLossTests in
// core/tests/test_blur_positions.py runs through a real reconcile. Add cases to both, with
// identical names, so a failure in one suite is findable in the other.
import assert from "node:assert/strict";
import test from "node:test";

import { pointsLostByRetiming } from "../../core/static/js/video-geometry.js";

// Every case starts from a blur spanning 10.0-15.0. `points` are the stored point times,
// including the first, which always sits on the old start.
export const CASES = [
  {
    name: "a pure move keeps every point",
    points: [10.0, 12.0, 15.0],
    newWindow: [20.0, 25.0],
    lost: 0,
  },
  {
    name: "a move backward keeps every point",
    points: [10.0, 12.0, 15.0],
    newWindow: [2.0, 7.0],
    lost: 0,
  },
  {
    name: "shrinking the right edge drops the trailing point",
    points: [10.0, 12.0, 15.0],
    newWindow: [10.0, 13.0],
    lost: 1,
  },
  {
    name: "shrinking the left edge keeps the last point before it",
    points: [10.0, 12.0, 15.0],
    newWindow: [13.0, 15.0],
    lost: 1,
  },
  {
    // ensure_first_position deletes the survivor rather than retiming it onto an occupied time,
    // so here the count comes from the occupant rule and not from the leading count alone.
    name: "shrinking the left edge onto an existing point drops the redundant one",
    points: [10.0, 12.0, 15.0],
    newWindow: [12.0, 15.0],
    lost: 1,
  },
  {
    name: "shrinking both edges drops one at each end but keeps a leading survivor",
    points: [10.0, 12.0, 15.0],
    newWindow: [11.0, 13.0],
    lost: 1,
  },
  {
    name: "several leading points collapse to one survivor",
    points: [10.0, 11.0, 12.0, 15.0],
    newWindow: [13.0, 15.0],
    lost: 2,
  },
  {
    // The case the pruning comment in reconcile_positions calls out: inside the tolerance this
    // reads as a move, so every point shifts by a hundredth - and the last one lands past an end
    // that did not move.
    name: "a start-only nudge inside the tolerance still shifts the last point out",
    points: [10.0, 12.0, 15.0],
    newWindow: [10.01, 15.0],
    lost: 1,
  },
  {
    // Nominally a duration change of exactly the tolerance; in binary it lands a hair *under*
    // (0.019999999999999574), putting it on the move side. Pinned because both implementations have
    // to fall the same side of the tolerance, whichever side that is.
    name: "a duration change at the tolerance boundary reads as a move in both languages",
    points: [10.0, 12.0, 15.0],
    newWindow: [10.02, 15.0],
    lost: 1,
  },
  {
    name: "a duration change past the tolerance is a resize and keeps the survivor",
    points: [10.0, 12.0, 15.0],
    newWindow: [10.03, 15.0],
    lost: 0,
  },
  {
    name: "a window clear of every point loses the whole path",
    points: [10.0, 12.0, 15.0],
    newWindow: [5.0, 8.0],
    lost: 3,
  },
];

for (const { name, points, newWindow, lost } of CASES) {
  test(`pointsLostByRetiming: ${name}`, () => {
    assert.equal(
      pointsLostByRetiming(points, 10.0, 15.0, newWindow[0], newWindow[1]),
      lost,
    );
  });
}

test("pointsLostByRetiming: an unchanged window loses nothing", () => {
  assert.equal(pointsLostByRetiming([10.0, 12.0, 15.0], 10.0, 15.0, 10.0, 15.0), 0);
});

test("pointsLostByRetiming: a right edge a hair under a whole number does not warn", () => {
  // Dragging the left handle computes the right edge as left+width, which lands just under the
  // stored value. Rounding first is what keeps this from warning about a loss the server, holding
  // 15.00, will never inflict.
  assert.equal(
    pointsLostByRetiming([10.0, 12.0, 15.0], 10.0, 15.0, 10.0, 14.999999999),
    0,
  );
});

test("pointsLostByRetiming: an unreadable old window is treated as no loss", () => {
  // The bar's data-start/data-end failed to parse. Guessing a count would put a number in front
  // of the user that nothing supports; the server still enforces the window either way.
  assert.equal(pointsLostByRetiming([10.0, 12.0], NaN, 15.0, 13.0, 15.0), 0);
  assert.equal(pointsLostByRetiming([10.0, 12.0], 10.0, NaN, 13.0, 15.0), 0);
});

test("pointsLostByRetiming: unparseable point times are ignored, not counted", () => {
  assert.equal(
    pointsLostByRetiming([10.0, NaN, 15.0], 10.0, 15.0, 10.0, 13.0),
    1,
    "the NaN dot contributes nothing; only the point at 15.0 is trailing",
  );
});

test("pointsLostByRetiming: no points means nothing to lose", () => {
  assert.equal(pointsLostByRetiming([], 10.0, 15.0, 11.0, 13.0), 0);
  assert.equal(pointsLostByRetiming(undefined, 10.0, 15.0, 11.0, 13.0), 0);
});
