import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";

// The 9:16 platform band (motion/README.md, "The 9:16 platform band"):
// TikTok draws its UI over a vertical everywhere outside y 168..1330, so a
// demo-register vertical puts its whole scene inside kit.css's pk-band
// window, which clips to that band. Checked on every kit vertical, parsed
// as a document (jsdom runs no script and loads nothing here): #stage holds
// exactly one element, the band, and every other element sits inside it.
// Words under the action rail (x > 900 below y 840) need a laid-out page to
// see; the vertical's request_render frames are where that is checked.

const here = dirname(fileURLToPath(import.meta.url));
const parse = (html) => new JSDOM(html).window.document;

const kitVerticals = readdirSync(here, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .filter((name) => existsSync(join(here, name, "vertical.html")))
  .map((name) => ({ composition: name, document: parse(readFileSync(join(here, name, "vertical.html"), "utf8")) }))
  .filter(({ document }) => document.querySelector('link[rel="stylesheet"][href="../../kit/kit.css"]'));

// kit.css's .pk-band rule, read through jsdom's CSS parser.
const pkBandRule = () => {
  const { document } = new JSDOM().window;
  const style = document.createElement("style");
  style.textContent = readFileSync(join(here, "..", "kit", "kit.css"), "utf8");
  document.head.append(style);
  return [...style.sheet.cssRules].find((rule) => rule.selectorText === ".pk-band");
};

describe("9:16 platform band", () => {
  it("kit.css's pk-band clips to y 168..1330", () => {
    const rule = pkBandRule();
    expect(rule).toBeDefined();
    expect(rule.style.top).toBe("168px");
    expect(rule.style.bottom).toBe("590px");
    expect(rule.style.overflow).toBe("hidden");
  });

  it("finds the kit verticals", () => {
    expect(kitVerticals.length).toBeGreaterThan(0);
  });

  it.each(kitVerticals)("$composition/vertical.html keeps its scene inside one pk-band", ({ document }) => {
    const stage = document.getElementById("stage");
    expect(stage).not.toBeNull();
    expect(stage.children).toHaveLength(1);
    const [band] = stage.children;
    expect(band.classList.contains("pk-band")).toBe(true);
    const outside = [...stage.querySelectorAll("*")].filter((el) => el !== band && !band.contains(el));
    expect(outside).toEqual([]);
  });
});
