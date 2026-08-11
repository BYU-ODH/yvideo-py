import assert from "node:assert/strict";
import test from "node:test";

import {
  formatSecondsToString,
  parseTimeStringToSeconds,
} from "../../core/static/js/utils.js";

// The same cases seconds2hms is held to in core/tests/test_core.py: the two have to agree, since
// the editor shows server-rendered and page-rendered times side by side.
test("formats seconds as H:MM:SS.SS", () => {
  assert.equal(formatSecondsToString(0), "0:00:00.00");
  assert.equal(formatSecondsToString(3723.5), "1:02:03.50");
  assert.equal(formatSecondsToString(59.999), "0:01:00.00");
});

test("parses what it formats, plus the bare seconds stored in data attributes", () => {
  assert.equal(parseTimeStringToSeconds("1:02:03.50"), 3723.5);
  assert.equal(parseTimeStringToSeconds("2:03.50"), 123.5);
  assert.equal(parseTimeStringToSeconds("8.5"), 8.5);
});

test("rejects what is not a time rather than reading it as one", () => {
  for (const entered of ["not a time", "", "1:", ":30", "1:2:3:4"]) {
    assert.ok(Number.isNaN(parseTimeStringToSeconds(entered)), `${entered} parsed`);
  }
});
