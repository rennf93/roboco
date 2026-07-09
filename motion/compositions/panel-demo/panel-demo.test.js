import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// HTML-structure smoke test for the panel-demo composition — mirrors
// release-announcement.test.js. We parse the HTML as text and assert
// structural invariants: dimensions, props.js wiring, the kit stylesheet
// link, timed clips, the kit's pill/toast/typing/cursor pieces actually
// being used, and the offline-render constraint (no CDN, no network URLs).

const here = dirname(fileURLToPath(import.meta.url));
const dir = here;
const read = (name) => readFileSync(join(dir, name), "utf8");

const orientations = [
  { file: "vertical.html", height: "1920" },
  { file: "square.html", height: "1080" },
];

describe("panel-demo composition", () => {
  it("ships props.js (default preview values, sidecar overwrites at render)", () => {
    expect(existsSync(join(dir, "props.js"))).toBe(true);
    const src = read("props.js");
    expect(src).toContain("window.__PROPS__");
    expect(src).toContain("window.__ORIENTATION__");
    expect(src).toContain("taskTitle");
    expect(src).toContain("toastTitle");
    expect(src).toContain("toastBody");
  });

  it.each(orientations)("$file parses with correct dimensions and wiring", ({ file, height }) => {
    const html = read(file);

    // HyperFrames render params.
    expect(html).toContain('data-width="1080"');
    expect(html).toContain(`data-height="${height}"`);
    expect(html).toMatch(/data-duration="12"/);
    expect(html).toMatch(/data-fps="30"/);

    // props.js loaded before the kit helper and before any inline script
    // reads window.__PROPS__ / window.PanelKit.
    const propsIdx = html.indexOf('<script src="props.js"></script>');
    const kitJsIdx = html.indexOf('<script src="../../kit/kit.js"></script>');
    const inlineIdx = html.indexOf("window.__timelines");
    expect(propsIdx).toBeGreaterThan(-1);
    expect(kitJsIdx).toBeGreaterThan(propsIdx);
    expect(inlineIdx).toBeGreaterThan(kitJsIdx);

    // Kit stylesheet linked (this composition has no theme.css of its own).
    expect(html).toContain('href="../../kit/kit.css"');

    // At least one timed clip element.
    expect(html).toMatch(/class="[^"]*clip[^"]*"/);

    // Panel frame chrome.
    expect(html).toContain("pk-frame");
    expect(html).toContain("pk-frame__sidebar");
    expect(html).toContain("pk-frame__topbar");

    // Typing reveal: container + caret present, wired to the kit helper.
    expect(html).toContain('id="typeTitle"');
    expect(html).toContain("pk-caret");
    expect(html).toContain("PanelKit.typeText");

    // Task card + the progress -> completed pill swap.
    expect(html).toContain("pk-card");
    expect(html).toContain("pk-pill--progress");
    expect(html).toContain("pk-pill--completed");

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

    // No CDN / network fetch of any kind — the render is offline.
    expect(html).not.toMatch(/<script[^>]+src="https?:\/\//);
    expect(html).not.toMatch(/href="https?:\/\//);
  });
});
