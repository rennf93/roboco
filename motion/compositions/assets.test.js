import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// motion/ is one HyperFrames project: the video-renderer renders with motion/
// as the project root, and the CLI (lint, preview) takes the nearest folder
// holding an index.html as the root. So every file a cut loads must sit inside
// motion/, and the only index.html is motion/index.html: one under
// compositions/<id>/ would become the CLI's root for that cut, and its
// ../../kit and ../../public paths would land outside it again. These are the
// lint errors invalid_parent_traversal_in_asset_path and missing_local_asset,
// checked without the CLI.

const here = dirname(fileURLToPath(import.meta.url));
const motionRoot = resolve(here, "..");
const SRC_OR_HREF = /\b(?:src|href)\s*=\s*(["'])(.*?)\1/gi;
const CSS_URL = /url\(\s*(["']?)(.*?)\1\s*\)/gi;
const EXTERNAL = /^(?:[a-z][a-z0-9+.-]*:|#|\/\/)/i;

const compositions = readdirSync(here, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => join(here, entry.name));
const filesIn = (dir, ext) =>
  readdirSync(dir)
    .filter((name) => name.endsWith(ext))
    .map((name) => join(dir, name));
const cuts = compositions.flatMap((dir) => filesIn(dir, ".html"));
const stylesheets = [join(motionRoot, "kit"), ...compositions].flatMap((dir) =>
  filesIn(dir, ".css"),
);

/** Every local path a file loads, as written. */
function localPaths(file) {
  const text = readFileSync(file, "utf8");
  const patterns = file.endsWith(".css") ? [CSS_URL] : [SRC_OR_HREF, CSS_URL];
  return patterns
    .flatMap((pattern) => [...text.matchAll(pattern)].map((match) => match[2].trim()))
    .filter((value) => value && !EXTERNAL.test(value))
    .map((value) => value.split(/[?#]/, 1)[0]);
}

const references = [...cuts, ...stylesheets].flatMap((file) =>
  localPaths(file).map((path) => ({
    from: relative(motionRoot, file),
    path,
    target: resolve(dirname(file), path),
  })),
);

describe("motion/ is one self-contained project", () => {
  it("finds the cuts, the stylesheets and their references", () => {
    expect(cuts.length).toBeGreaterThan(0);
    expect(stylesheets.length).toBeGreaterThan(0);
    expect(references.length).toBeGreaterThan(0);
  });

  it.each(references)("$from loads $path from inside motion/", ({ target }) => {
    expect(target.startsWith(`${motionRoot}${sep}`)).toBe(true);
    expect(existsSync(target)).toBe(true);
  });

  it("has its project root marker, motion/index.html, with content", () => {
    const index = join(motionRoot, "index.html");
    expect(existsSync(index)).toBe(true);
    expect(readFileSync(index, "utf8")).toMatch(/data-composition-id="[^"]+"/);
  });

  it("has no index.html under compositions/", () => {
    expect(compositions.filter((dir) => existsSync(join(dir, "index.html")))).toEqual([]);
  });
});
