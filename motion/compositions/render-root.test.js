import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The render root contract, checked on every cut of every composition.
// @hyperframes/producer (in the video-renderer) and the HyperFrames runtime in
// the page both take the FIRST element carrying data-composition-id as the
// composition root, and the producer sizes the render from that element's
// data-width/data-height, falling back to 1080x1920 when they are absent.
// When <html> carried the id too, it became the root and every square cut
// rendered at 1080x1920. So the id sits on exactly one element, #stage (which
// is also what HyperFrames lint requires: duplicate_composition_id), and that
// element declares the cut's own size.

const here = dirname(fileURLToPath(import.meta.url));
const CUT_HEIGHTS = { "vertical.html": "1920", "square.html": "1080" };
const COMPOSITION_ROOT_TAG = /<[a-z][a-z0-9-]*\b[^>]*\bdata-composition-id=[^>]*>/gi;

const attr = (tag, name) => new RegExp(`\\b${name}="([^"]*)"`).exec(tag)?.[1];

const cuts = readdirSync(here, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .flatMap((entry) =>
    Object.keys(CUT_HEIGHTS)
      .filter((file) => existsSync(join(here, entry.name, file)))
      .map((file) => ({ composition: entry.name, file })),
  );

describe("composition render root", () => {
  it("finds the cuts", () => {
    expect(cuts.length).toBeGreaterThan(0);
  });

  it.each(cuts)("$composition/$file has one sized root, #stage", ({ composition, file }) => {
    const html = readFileSync(join(here, composition, file), "utf8");
    const roots = html.match(COMPOSITION_ROOT_TAG) ?? [];
    expect(roots).toHaveLength(1);
    const [root] = roots;
    expect(attr(root, "id")).toBe("stage");
    expect(attr(root, "data-composition-id")).toBe(composition);
    expect(attr(root, "data-width")).toBe("1080");
    expect(attr(root, "data-height")).toBe(CUT_HEIGHTS[file]);
  });
});
