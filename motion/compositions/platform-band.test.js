import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The 9:16 platform band (motion/README.md, "The 9:16 platform band"):
// TikTok draws its UI over a vertical everywhere outside y 168..1330, so a
// demo-register vertical puts its whole scene inside kit.css's pk-band
// window, which clips to that band. Checked on every kit vertical: #stage
// holds exactly one child, the band, and every other element sits inside it.
// Words under the action rail (x > 900 below y 840) need a laid-out page to
// see; the vertical's request_render frames are where that is checked.

const here = dirname(fileURLToPath(import.meta.url));
const kitCss = readFileSync(join(here, "..", "kit", "kit.css"), "utf8");

const kitVerticals = readdirSync(here, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .filter((name) => existsSync(join(here, name, "vertical.html")))
  .map((name) => ({ composition: name, html: readFileSync(join(here, name, "vertical.html"), "utf8") }))
  .filter(({ html }) => html.includes('href="../../kit/kit.css"'));

// The markup between #stage's opening tag and its closing </div>, comments
// dropped. A cut's #stage is the body's only element.
const stageInner = (html) => {
  const body = html.slice(html.indexOf("<body>"), html.indexOf("<script>", html.indexOf("<body>")));
  const open = /<div\b[^>]*\sid="stage"[^>]*>/.exec(body);
  const end = body.lastIndexOf("</div>");
  return body.slice(open.index + open[0].length, end).replace(/<!--[\s\S]*?-->/g, "").trim();
};

describe("9:16 platform band", () => {
  it("kit.css's pk-band clips to y 168..1330", () => {
    const rule = /\.pk-band\s*\{([^}]*)\}/.exec(kitCss);
    expect(rule).not.toBeNull();
    expect(rule[1]).toMatch(/top:\s*168px;/);
    expect(rule[1]).toMatch(/bottom:\s*590px;/);
    expect(rule[1]).toMatch(/overflow:\s*hidden;/);
  });

  it("finds the kit verticals", () => {
    expect(kitVerticals.length).toBeGreaterThan(0);
  });

  it.each(kitVerticals)("$composition/vertical.html keeps its scene inside one pk-band", ({ html }) => {
    const inner = stageInner(html);
    const band = /^<div\b[^>]*\sclass="pk-band"[^>]*>/.exec(inner);
    expect(band).not.toBeNull();
    // The band closes last: nothing in #stage sits beside it, outside the window.
    expect(inner.endsWith("</div>")).toBe(true);
    let depth = 0;
    for (const tag of inner.matchAll(/<(\/?)div\b[^>]*>/g)) {
      depth += tag[1] ? -1 : 1;
      if (depth === 0) expect(tag.index + tag[0].length).toBe(inner.length);
    }
  });
});
