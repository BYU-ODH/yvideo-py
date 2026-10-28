// Run with:  node --test tests/js/
//
// isHexColor decides whether the editor previews and saves a typed font color, so it has to
// accept exactly what validate_font_color in core/models.py accepts.
import assert from "node:assert/strict";
import test from "node:test";

import { isHexColor } from "../../core/static/js/ColorCombobox.js";

const CASES = [
  {value: "abc", valid: true},
  {value: "a1b2c3", valid: true},
  {value: "ABCDEF", valid: true},
  {value: "", valid: false},
  {value: "ab", valid: false},
  {value: "abcd", valid: false},
  {value: "abcde", valid: false},
  {value: "abcdefa", valid: false},
  {value: "#abc", valid: false},
  {value: "ggg", valid: false},
  {value: " abc", valid: false},
];

for (const {value, valid} of CASES) {
  test(`isHexColor(${JSON.stringify(value)}) is ${valid}`, () => {
    assert.equal(isHexColor(value), valid);
  });
}
