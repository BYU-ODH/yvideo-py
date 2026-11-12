// Run with:  node --test tests/js/
//
// The sequencing rules the IC control window follows (ic/static/ic/js/ic-protocol.js).
import assert from "node:assert/strict";
import test from "node:test";

import {
  afterEnded,
  annotationsAt,
  clampSeek,
  describeAnnotation,
  firstPlayableIndex,
  formatClock,
  nextAnnotationAfter,
  nextPlayableIndex,
  outputLost,
  OUTPUT_LOST_MS,
} from "../../ic/static/ic/js/ic-protocol.js";

const loop = { id: 1, mode: "loop", auto_advance: false, playable: true };
const featureManual = { id: 2, mode: "once", auto_advance: false, playable: true };
const featureAuto = { id: 3, mode: "once", auto_advance: true, playable: true };
const broken = { id: 4, mode: "once", auto_advance: false, playable: false };

test("a loop never advances on its own", () => {
  assert.deepEqual(afterEnded([loop, featureManual], 0), { action: "loop", nextIndex: 0 });
});

test("play-once items wait for the operator unless they auto-advance", () => {
  assert.deepEqual(afterEnded([featureManual, featureAuto], 0), { action: "idle", nextIndex: 1 });
  assert.deepEqual(afterEnded([featureAuto, featureManual], 0), { action: "advance", nextIndex: 1 });
});

test("auto-advance at the end of the program goes idle", () => {
  assert.deepEqual(afterEnded([featureAuto], 0), { action: "idle", nextIndex: null });
});

test("unplayable items are skipped when stepping", () => {
  const items = [loop, broken, featureManual];
  assert.equal(nextPlayableIndex(items, 0, 1), 2);
  assert.equal(nextPlayableIndex(items, 2, -1), 0);
  assert.equal(nextPlayableIndex(items, 2, 1), null);
  assert.equal(firstPlayableIndex([broken, featureAuto]), 1);
  assert.deepEqual(afterEnded([featureAuto, broken, featureManual], 0), { action: "advance", nextIndex: 2 });
});

test("seeking is clamped to the film", () => {
  assert.equal(clampSeek(-3, 100), 0);
  assert.equal(clampSeek(Number.NaN, 100), 0);
  assert.equal(clampSeek(500, 100), 99.95);
  assert.equal(clampSeek(12, null), 12);
});

test("active and next annotations", () => {
  const annotations = [
    { type: "mute", label: "Words", start: 5, end: 7 },
    { type: "skip", label: "Logo", start: 1, end: 3 },
    { type: "screenBlur", label: "", start: 6, end: 9 },
  ];
  assert.deepEqual(annotationsAt(annotations, 6).map(describeAnnotation), ["mute: Words", "blur"]);
  assert.deepEqual(annotationsAt(annotations, 7).map((a) => a.type), ["screenBlur"]);
  assert.equal(nextAnnotationAfter(annotations, 0).label, "Logo");
  assert.equal(nextAnnotationAfter(annotations, 6), null);
});

test("clock formatting", () => {
  assert.equal(formatClock(0), "0:00:00");
  assert.equal(formatClock(3725.9), "1:02:05");
  assert.equal(formatClock(-1), "0:00:00");
});

test("the output counts as lost only after the threshold, and only when it was connected", () => {
  assert.equal(outputLost(1000, 1000 + OUTPUT_LOST_MS, true), false);
  assert.equal(outputLost(1000, 1001 + OUTPUT_LOST_MS, true), true);
  assert.equal(outputLost(1000, 99999, false), false);
});
