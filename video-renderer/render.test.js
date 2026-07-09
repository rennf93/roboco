// node --test: node's built-in runner, no dependency needed. Only parseFps is
// exercised (a pure function) — renderComposition needs a real Chromium
// render and is covered by manual/e2e verification instead.
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseFps } from "./render.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const compositionsRoot = path.join(__dirname, "..", "motion", "compositions");

test("parseFps reads data-fps off a real composition file", async () => {
  const html = await readFile(
    path.join(compositionsRoot, "release-announcement", "vertical.html"),
    "utf8",
  );
  assert.equal(parseFps(html), 30);
});

test("parseFps reads a declared 24fps composition", () => {
  const html = `<div id="stage" data-fps="24"></div>`;
  assert.equal(parseFps(html), 24);
});

test("parseFps falls back to 30 when the attribute is missing", () => {
  assert.equal(parseFps("<html><body>no fps here</body></html>"), 30);
});

test("parseFps clamps below the 24-60 bound to the 30 fallback", () => {
  assert.equal(parseFps(`<div data-fps="15"></div>`), 30);
});

test("parseFps clamps above the 24-60 bound to the 30 fallback", () => {
  assert.equal(parseFps(`<div data-fps="120"></div>`), 30);
});

test("parseFps falls back to 30 on an unparsable value", () => {
  assert.equal(parseFps(`<div data-fps="abc"></div>`), 30);
});

test("parseFps accepts the boundary values 24 and 60", () => {
  assert.equal(parseFps(`<div data-fps="24"></div>`), 24);
  assert.equal(parseFps(`<div data-fps="60"></div>`), 60);
});
