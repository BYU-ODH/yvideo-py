// Run with:  node --test tests/js/
//
// pickSnapEdge decides when a dragged timeline edge snaps to the playhead (#443). Edges and target
// are pixel positions.
import assert from "node:assert/strict";
import test from "node:test";

import {
  SNAP_ACQUIRE_PX,
  SNAP_RELEASE_PX,
  nearestEdge,
  pickSnapEdge,
} from "../../core/static/js/timeline-snap.js";

const TARGET = 500;

test("an edge within the acquire distance snaps", () => {
  assert.equal(pickSnapEdge([TARGET + SNAP_ACQUIRE_PX], TARGET), 0);
  assert.equal(pickSnapEdge([TARGET - SNAP_ACQUIRE_PX], TARGET), 0);
});

test("an edge beyond the acquire distance does not snap", () => {
  assert.equal(pickSnapEdge([TARGET + SNAP_ACQUIRE_PX + 1], TARGET), -1);
});

test("a snapped edge holds until it passes the release distance", () => {
  const between = TARGET + (SNAP_ACQUIRE_PX + SNAP_RELEASE_PX) / 2;
  assert.equal(pickSnapEdge([between], TARGET, 0), 0, "held inside the release distance");
  assert.equal(pickSnapEdge([between], TARGET, -1), -1, "not caught there when unsnapped");
  assert.equal(pickSnapEdge([TARGET + SNAP_RELEASE_PX + 1], TARGET, 0), -1, "released beyond it");
});

test("the closer of two edges wins", () => {
  assert.equal(pickSnapEdge([TARGET - 6, TARGET + 2], TARGET), 1);
  assert.equal(pickSnapEdge([TARGET - 1, TARGET + 2], TARGET), 0);
});

test("a held snap is not stolen by the other edge", () => {
  assert.equal(pickSnapEdge([TARGET + 10, TARGET + 1], TARGET, 0), 0);
});

test("null edges are never snapped", () => {
  assert.equal(pickSnapEdge([null, TARGET + 30], TARGET), -1);
  assert.equal(pickSnapEdge([null], TARGET, 0), -1, "a held edge that becomes invalid lets go");
  assert.equal(pickSnapEdge([null, TARGET + 1], TARGET), 1);
});

test("nearestEdge picks the closest non-null edge", () => {
  assert.equal(nearestEdge([100, 450], TARGET), 1);
  assert.equal(nearestEdge([480, 900], TARGET), 0);
  assert.equal(nearestEdge([300, null], TARGET), 0);
});
