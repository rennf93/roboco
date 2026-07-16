// node --test: exercises the pure 'frames' form-field validation only —
// server.js's app.listen is guarded (see server.js) so importing it here
// never binds the real port.
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseFramesField } from "./server.js";
import { MAX_PREVIEW_FRAMES } from "./render.js";

test("parseFramesField treats an absent field as the existing MP4 path", () => {
  assert.deepEqual(parseFramesField(undefined), { count: null });
});

test("parseFramesField treats an empty string as the existing MP4 path", () => {
  assert.deepEqual(parseFramesField(""), { count: null });
});

test("parseFramesField accepts an in-bounds integer string", () => {
  assert.deepEqual(parseFramesField("8"), { count: 8 });
});

test("parseFramesField accepts the boundary values 1 and MAX_PREVIEW_FRAMES", () => {
  assert.deepEqual(parseFramesField("1"), { count: 1 });
  assert.deepEqual(parseFramesField(String(MAX_PREVIEW_FRAMES)), {
    count: MAX_PREVIEW_FRAMES,
  });
});

test("parseFramesField rejects zero", () => {
  assert.ok(parseFramesField("0").error);
});

test("parseFramesField rejects above MAX_PREVIEW_FRAMES", () => {
  assert.ok(parseFramesField(String(MAX_PREVIEW_FRAMES + 1)).error);
});

test("parseFramesField rejects a non-integer value", () => {
  assert.ok(parseFramesField("4.5").error);
});

test("parseFramesField rejects a non-numeric value", () => {
  assert.ok(parseFramesField("abc").error);
});
