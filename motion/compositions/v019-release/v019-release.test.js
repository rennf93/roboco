import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// HTML-structure smoke test for the v019-release composition. Mirrors the
// release-announcement test contract: dimensions, props.js wiring, theme
// link, timed clips, and the offline-render constraint (no CDN scripts).

const here = dirname(fileURLToPath(import.meta.url));
const dir = here;
const read = (name) => readFileSync(join(dir, name), "utf8");

const orientations = [
  { file: "vertical.html", height: "1920" },
  { file: "square.html", height: "1080" },
];

describe("v019-release composition", () => {
  it("ships props.js (default preview values, sidecar overwrites at render)", () => {
    expect(existsSync(join(dir, "props.js"))).toBe(true);
    const src = read("props.js");
    expect(src).toContain("window.__PROPS__");
    expect(src).toContain("window.__ORIENTATION__");
  });

  it("ships theme.css", () => {
    expect(existsSync(join(dir, "theme.css"))).toBe(true);
  });

  it.each(orientations)("$file parses with correct dimensions and wiring", ({ file, height }) => {
    const html = read(file);

    // HyperFrames render params on <html> and <div id="stage">.
    expect(html).toContain('data-composition-id="v019-release"');
    expect(html).toContain('data-width="1080"');
    expect(html).toContain(`data-height="${height}"`);
    expect(html).toMatch(/data-duration="\d+"/);
    expect(html).toMatch(/data-fps="\d+"/);

    // props.js is loaded before the inline script reads window.__PROPS__.
    expect(html).toContain('<script src="props.js"></script>');

    // theme.css is linked.
    expect(html).toContain('href="theme.css"');

    // At least one timed clip element.
    expect(html).toMatch(/class="[^"]*clip[^"]*"/);

    // No CDN scripts — the render is offline. Fonts load via @font-face from
    // ../../public/fonts/, never from a CDN.
    expect(html).not.toMatch(/<script[^>]+src="https?:\/\//);
  });
});
