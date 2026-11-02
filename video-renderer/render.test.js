// node --test: node's built-in runner, no dependency needed. The pure pieces
// (parseFps, resolveRenderTarget) and the tar extraction are exercised here;
// renderComposition needs a real Chromium render and is covered by manual/e2e
// verification instead.
import { existsSync, lstatSync } from "node:fs";
import {
  link,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";
import * as tar from "tar";
import {
  UnknownCompositionError,
  extractTar,
  parseFps,
  resolveRenderTarget,
} from "./render.js";

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

test("resolveRenderTarget renders a cut from motion/ as the project root", () => {
  const target = resolveRenderTarget("/tmp/x", "release-0.25.0", "square");
  assert.deepEqual(target, {
    projectDir: "/tmp/x/motion",
    compositionDir: "/tmp/x/motion/compositions/release-0.25.0",
    entryFile: "compositions/release-0.25.0/square.html",
  });
});

test("resolveRenderTarget refuses an id that leaves the compositions folder", () => {
  for (const compositionId of ["..", "../kit", "a/../../public", ".", ""]) {
    assert.throws(
      () => resolveRenderTarget("/tmp/x", compositionId, "vertical"),
      UnknownCompositionError,
      compositionId,
    );
  }
});

test("extractTar drops symlinks and hard links, keeps files and folders", async (t) => {
  const work = await mkdtemp(path.join(tmpdir(), "render-test-"));
  t.after(() => rm(work, { recursive: true, force: true }));
  const source = path.join(work, "source");
  await mkdir(path.join(source, "motion", "public"), { recursive: true });
  await writeFile(path.join(source, "motion", "public", "logo.png"), "png");
  await symlink("/etc/passwd", path.join(source, "motion", "public", "passwd"));
  await link(
    path.join(source, "motion", "public", "logo.png"),
    path.join(source, "motion", "public", "logo-hardlink.png"),
  );
  const tarPath = path.join(work, "motion.tar");
  // In this order, so logo.png is packed as the file and the second name as
  // the hard link to it.
  await tar.create({ cwd: source, file: tarPath }, [
    "motion/public/logo.png",
    "motion/public/logo-hardlink.png",
    "motion/public/passwd",
  ]);
  const types = [];
  await tar.list({ file: tarPath, onReadEntry: (entry) => types.push(entry.type) });
  assert.ok(types.includes("SymbolicLink") && types.includes("Link"), types.join());

  const dest = path.join(work, "dest");
  await mkdir(dest);
  await extractTar(await readFile(tarPath), dest);

  const extracted = path.join(dest, "motion", "public");
  assert.equal(await readFile(path.join(extracted, "logo.png"), "utf8"), "png");
  assert.ok(lstatSync(path.join(extracted, "logo.png")).isFile());
  assert.equal(existsSync(path.join(extracted, "passwd")), false);
  assert.equal(existsSync(path.join(extracted, "logo-hardlink.png")), false);
});
