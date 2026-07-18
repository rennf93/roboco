import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// HTML-structure smoke test for the release-recap composition - mirrors
// panel-demo.test.js (this clip extends the same pk-* kit). We parse the
// HTML as text and assert structural invariants: dimensions, props.js
// wiring, the kit stylesheet link, the structural-only clip-window
// allowlist, the three-release beats (card + chip + pill flip each), the
// cursor, the toast/outro, the offline constraint, and the captions.json
// schema/limits/copy regressions the CEO's revision called out on the prior
// text-card composition.

const here = dirname(fileURLToPath(import.meta.url));
const dir = here;
const read = (name) => readFileSync(join(dir, name), "utf8");

const orientations = [
  { file: "vertical.html", height: "1920" },
  { file: "square.html", height: "1080" },
];

const versions = ["v0.18.0", "v0.19.0", "v0.20.0"];

describe("release-recap composition", () => {
  it("ships props.js (default preview values, sidecar overwrites at render)", () => {
    expect(existsSync(join(dir, "props.js"))).toBe(true);
    const src = read("props.js");
    expect(src).toContain("window.__PROPS__");
    expect(src).toContain("window.__ORIENTATION__");
    expect(src).toContain("introText");
    expect(src).toContain("toastTitle");
    expect(src).toContain("toastBody");
    expect(src).not.toMatch(/—/); // no em dash in on-screen copy
  });

  it.each(orientations)("$file parses with correct dimensions and wiring", ({ file, height }) => {
    const html = read(file);

    // HyperFrames render params.
    expect(html).toContain('data-width="1080"');
    expect(html).toContain(`data-height="${height}"`);
    expect(html).toMatch(/data-duration="18"/);
    expect(html).toMatch(/data-fps="30"/);

    // props.js loaded before the kit helper and before any inline script
    // reads window.__PROPS__ / window.PanelKit.
    const propsIdx = html.indexOf('<script src="props.js"></script>');
    const kitJsIdx = html.indexOf('<script src="../../kit/kit.js"></script>');
    const inlineIdx = html.indexOf("window.__timelines");
    expect(propsIdx).toBeGreaterThan(-1);
    expect(kitJsIdx).toBeGreaterThan(propsIdx);
    expect(inlineIdx).toBeGreaterThan(kitJsIdx);

    // Kit stylesheet linked (no theme.css of its own - this is the demo
    // register, not the text-card register).
    expect(html).toContain('href="../../kit/kit.css"');

    // Clip windows are for structural layers only (motion/README.md's
    // clip-window rule - the renderer's per-clip scheduler drifts behind the
    // encoded timeline on long compositions and silently drops the tail).
    // Only the cold-open hero, the full-length panel frame, and its
    // duration-matching status indicator may carry class="clip"; every beat
    // (intake, cards, pill swaps, cursor, stats, toast, outro) drives via
    // base-hidden + delayed CSS animation instead.
    const clipClasses = [...html.matchAll(/class="([^"]*\bclip\b[^"]*)"/g)].map((m) => m[1]);
    expect(clipClasses.length).toBe(3);
    expect(new Set(clipClasses)).toEqual(new Set(["rc-hero clip", "pk-frame clip", "pk-frame__status clip"]));

    // Panel frame chrome - "the video must show the product moving".
    expect(html).toContain("pk-frame");
    expect(html).toContain("pk-frame__sidebar");
    expect(html).toContain("pk-frame__topbar");

    // Typing reveal into intake.
    expect(html).toContain('id="typeIntro"');
    expect(html).toContain("pk-caret");
    expect(html).toContain("typeReveal");

    // Three release beats: a card + version chip + a progress -> completed
    // pill flip each ("status pills flipping", plural). Cards are
    // beat-level (base-hidden + animation-delay), never class="clip".
    const cardCount = (html.match(/class="pk-card"/g) || []).length;
    const progressCount = (html.match(/pk-pill--progress/g) || []).length;
    const completedCount = (html.match(/pk-pill--completed/g) || []).length;
    expect(cardCount).toBe(3);
    expect(progressCount).toBe(3);
    expect(completedCount).toBe(3);
    for (const version of versions) {
      expect(html).toContain(version);
    }

    // Cursor glide + click.
    expect(html).toContain("pk-cursor");
    expect(html).toContain("pk-cursor__ring");

    // Toast.
    expect(html).toContain("pk-toast");
    expect(html).toContain('id="toastTitle"');
    expect(html).toContain('id="toastBody"');

    // Outro headline moment.
    expect(html).toContain("pk-outro");
    expect(html).toContain("roboco.tech");

    // No CDN / network fetch of any kind - the render is offline.
    expect(html).not.toMatch(/<script[^>]+src="https?:\/\//);
    expect(html).not.toMatch(/href="https?:\/\//);

    // No em dash in on-screen copy (design-bar regression from the prior
    // text-card composition's QA failure on this same task).
    expect(html).not.toMatch(/—/);
  });

  it("ships captions.json within X and TikTok platform limits", () => {
    expect(existsSync(join(dir, "captions.json"))).toBe(true);
    const captions = JSON.parse(read("captions.json"));

    expect(captions.composition_id).toBe("release-recap");
    expect(captions.platforms.x.char_count).toBe(captions.platforms.x.caption.length);
    expect(captions.platforms.x.char_count).toBeLessThanOrEqual(captions.platforms.x.limit);
    expect(captions.platforms.x.within_limit).toBe(true);
    expect(captions.platforms.tiktok.char_count).toBe(captions.platforms.tiktok.caption.length);
    expect(captions.platforms.tiktok.char_count).toBeLessThanOrEqual(captions.platforms.tiktok.limit);
    expect(captions.platforms.tiktok.within_limit).toBe(true);

    // No em dash in either caption.
    expect(captions.platforms.x.caption).not.toMatch(/—/);
    expect(captions.platforms.tiktok.caption).not.toMatch(/—/);
  });
});
