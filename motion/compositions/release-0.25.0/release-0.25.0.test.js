import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// HTML-structure smoke test for the RoboCo v0.25.0 release composition.
// Extends the panel-demo register: panel chrome, typing reveal, four feature cards
// with progress -> completed pill flips, cursor, stats overlay, toast,
// outro, offline constraint, and the captions.json schema/limits.

const here = dirname(fileURLToPath(import.meta.url));
const dir = here;
const read = (name) => readFileSync(join(dir, name), "utf8");

const orientations = [
  { file: "vertical.html", height: "1920" },
  { file: "square.html", height: "1080" },
];

describe("release-0.25.0 composition", () => {
  it("ships props.js (default preview values, sidecar overwrites at render)", () => {
    expect(existsSync(join(dir, "props.js"))).toBe(true);
    const src = read("props.js");
    expect(src).toContain("window.__PROPS__");
    expect(src).toContain("window.__ORIENTATION__");
    expect(src).toContain("introText");
    expect(src).toContain("toastTitle");
    expect(src).toContain("toastBody");
    expect(src).not.toMatch(/—/);
  });

  it.each(orientations)("$file parses with correct dimensions and wiring", ({ file, height }) => {
    const html = read(file);

    expect(html).toContain('data-width="1080"');
    expect(html).toContain(`data-height="${height}"`);
    expect(html).toMatch(/data-duration="40"/);
    expect(html).toMatch(/data-fps="30"/);

    const propsIdx = html.indexOf('<script src="props.js"></script>');
    const kitJsIdx = html.indexOf('<script src="../../kit/kit.js"></script>');
    const inlineIdx = html.indexOf("window.__timelines");
    expect(propsIdx).toBeGreaterThan(-1);
    expect(kitJsIdx).toBeGreaterThan(propsIdx);
    expect(inlineIdx).toBeGreaterThan(kitJsIdx);

    expect(html).toContain('href="../../kit/kit.css"');
    expect(html).toMatch(/class="[^"]*clip[^"]*"/);

    expect(html).toContain("pk-frame");
    expect(html).toContain("pk-frame__sidebar");
    expect(html).toContain("pk-frame__topbar");

    expect(html).toContain('id="typeIntro"');
    expect(html).toContain("pk-caret");
    expect(html).toContain("typeReveal");

    const cardCount = (html.match(/pk-card clip/g) || []).length;
    const progressCount = (html.match(/pk-pill--progress/g) || []).length;
    const completedCount = (html.match(/pk-pill--completed/g) || []).length;
    expect(cardCount).toBe(4);
    expect(progressCount).toBe(4);
    expect(completedCount).toBe(4);

    expect(html).toContain("v0.25.0");
    expect(html).toContain("Env ladder");
    expect(html).toContain("Collision map");
    expect(html).toContain("Metrics donut");
    expect(html).toContain("Notification bell");

    expect(html).toContain("pk-cursor");
    expect(html).toContain("pk-cursor__ring");

    expect(html).toContain("pk-toast");
    expect(html).toContain('id="toastTitle"');
    expect(html).toContain('id="toastBody"');

    expect(html).toContain("pk-outro");
    expect(html).toContain("roboco.tech");

    expect(html).not.toMatch(/<script[^>]+src="https?:\/\//);
    expect(html).not.toMatch(/href="https?:\/\//);
    expect(html).not.toMatch(/—/);
  });

  it("ships captions.json within X and TikTok platform limits", () => {
    expect(existsSync(join(dir, "captions.json"))).toBe(true);
    const captions = JSON.parse(read("captions.json"));

    expect(captions.composition_id).toBe("release-0.25.0");
    expect(captions.platforms.x.char_count).toBe(captions.platforms.x.caption.length);
    expect(captions.platforms.x.char_count).toBeLessThanOrEqual(captions.platforms.x.limit);
    expect(captions.platforms.x.within_limit).toBe(true);
    expect(captions.platforms.tiktok.char_count).toBe(captions.platforms.tiktok.caption.length);
    expect(captions.platforms.tiktok.char_count).toBeLessThanOrEqual(captions.platforms.tiktok.limit);
    expect(captions.platforms.tiktok.within_limit).toBe(true);

    expect(captions.platforms.x.caption).not.toMatch(/—/);
    expect(captions.platforms.tiktok.caption).not.toMatch(/—/);
  });
});
