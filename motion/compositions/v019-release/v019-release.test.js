import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// HTML-structure smoke test for the v019-release composition. Mirrors the
// release-announcement test contract: dimensions, props.js wiring, theme
// link, timed clips, the offline-render constraint (no CDN scripts), plus
// the design-bar "no em dash in on-screen copy" rule and verifiable captions.

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

    // No CDN scripts - the render is offline. Fonts load via @font-face from
    // ../../public/fonts/, never from a CDN.
    expect(html).not.toMatch(/<script[^>]+src="https?:\/\//);
  });

  it("has no em dashes in on-screen copy", () => {
    const propsSrc = read("props.js");
    const vertical = read("vertical.html");
    const square = read("square.html");

    // Design-bar rule: no em dash in product copy.
    expect(propsSrc).not.toContain("—");
    expect(vertical).not.toContain("—");
    expect(square).not.toContain("—");
  });

  it("ships captions.json with X and TikTok captions within platform limits", () => {
    const captionsPath = join(dir, "captions.json");
    expect(existsSync(captionsPath)).toBe(true);

    const captions = JSON.parse(readFileSync(captionsPath, "utf8"));
    expect(captions.composition_id).toBe("v019-release");
    expect(captions.version).toBe("0.19.0");

    for (const platform of ["x", "tiktok"]) {
      expect(captions.platforms[platform]).toBeDefined();
      const entry = captions.platforms[platform];
      expect(typeof entry.caption).toBe("string");
      expect(entry.caption.length).toBe(entry.char_count);
      expect(entry.char_count).toBeLessThanOrEqual(entry.limit);
      expect(entry.within_limit).toBe(true);
    }
  });

  it("does not commit an npm package-lock.json in the pnpm-managed motion package", () => {
    const lockfilePath = join(dir, "..", "..", "package-lock.json");
    expect(existsSync(lockfilePath)).toBe(false);
  });
});
