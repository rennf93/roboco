import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The animation clock rule (motion/README.md, "The animation clock"), checked
// statically on every cut of every composition.
//
// A CSS animation's clock starts at the data-start of the nearest element
// that carries one, the element itself included, and animation-delay counts
// from there. HyperFrames 0.7.36 times an animated element from its own
// data-start (0 when it has none); 0.8 times it from
// el.closest("[data-start]"). The two agree only when no animated element
// sits, without a data-start of its own, inside a clip that starts after 0.
//
// R1: an animated element with no data-start of its own must not inherit a
//     non-zero start from an ancestor (it would move between runtimes).
// R2: an element with data-start > 0 must not carry an animation-delay equal
//     to that start (the two add, so the entrance runs at twice the time).
//     The delay may come from the element's inline style, a stylesheet rule,
//     an inline --pk-*-delay, or the first time of its data-waypoints /
//     data-shots (kit.js makes that the generated animation's delay).
//
// "Animated" means any of: an inline animation* or --pk-*-delay; a rule with
// an animation in a linked stylesheet (kit.css, theme.css) or the cut's own
// <style> whose subject matches the element; a PanelKit.typeText target; a
// .pk-cursor[data-waypoints] or .pk-camera[data-shots] (kit.js generates
// their animations at load).

const here = dirname(fileURLToPath(import.meta.url));
const CUT_FILES = ["vertical.html", "square.html"];
const RULE_POINTER = 'see motion/README.md, "The animation clock"';

// Kept as shipped: these cuts double their beat timings today (panel-demo's
// card, pills, cursor, toast and outro; release-announcement's headline,
// script and outro), and repairing that is a visible change that needs the
// CEO. Drop an entry once its composition is repaired; the test below fails
// when an entry no longer violates R2, so the list cannot go stale.
const R2_KEPT_AS_SHIPPED = new Set(["panel-demo", "release-announcement"]);

// ---- HTML: a small tag-stack parser (compositions are plain, well-formed HTML)

const VOID_TAGS = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
const RAW_TEXT_TAGS = new Set(["script", "style"]);
// An unquoted attribute value excludes quotes, =, <, > and ` (as in the HTML
// spec), so it can never also read as a quoted value: each attribute list has
// one parse, and TOKEN cannot backtrack exponentially on a malformed tag.
const TOKEN = /<!--[\s\S]*?-->|<![^>]*>|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*(\/?)>/g;
const ATTRIBUTE = /([^\s=>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

function parseAttributes(source) {
  const attrs = {};
  for (const m of source.matchAll(ATTRIBUTE)) {
    attrs[m[1].toLowerCase()] = m[2] ?? m[3] ?? m[4] ?? "";
  }
  return attrs;
}

function parseHtml(html) {
  const root = { tag: "#document", attrs: {}, parent: null };
  const elements = [];
  const scripts = [];
  const styles = [];
  const links = [];
  let current = root;
  TOKEN.lastIndex = 0;
  let m;
  while ((m = TOKEN.exec(html))) {
    if (m[1]) {
      const tag = m[1].toLowerCase();
      let open = current;
      while (open !== root && open.tag !== tag) open = open.parent;
      if (open !== root) current = open.parent;
      continue;
    }
    if (!m[2]) continue; // comment or doctype
    const tag = m[2].toLowerCase();
    const element = { tag, attrs: parseAttributes(m[3] ?? ""), parent: current };
    if (RAW_TEXT_TAGS.has(tag)) {
      const end = html.toLowerCase().indexOf(`</${tag}`, TOKEN.lastIndex);
      const body = html.slice(TOKEN.lastIndex, end < 0 ? html.length : end);
      (tag === "script" ? scripts : styles).push(body);
      TOKEN.lastIndex = end < 0 ? html.length : html.indexOf(">", end) + 1;
      continue;
    }
    if (tag === "link" && (element.attrs.rel ?? "").toLowerCase() === "stylesheet") {
      links.push(element.attrs.href);
    }
    elements.push(element);
    if (!VOID_TAGS.has(tag) && m[4] !== "/") current = element;
  }
  return { elements, scripts, styles, links };
}

const classesOf = (element) => (element.attrs.class ?? "").split(/\s+/).filter(Boolean);

function describeElement(element) {
  const id = element.attrs.id ? `#${element.attrs.id}` : "";
  const classes = classesOf(element).map((c) => `.${c}`).join("");
  return `<${element.tag}${id}${classes}>`;
}

// ---- CSS: rules, declarations, animation delays

function splitTopLevel(text, separator) {
  const parts = [];
  let depth = 0;
  let quote = null;
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") quote = ch;
    else if (ch === "(") depth++;
    else if (ch === ")") depth--;
    else if (depth === 0 && (separator === " " ? /\s/.test(ch) : ch === separator)) {
      parts.push(text.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(text.slice(start));
  return parts.map((p) => p.trim()).filter(Boolean);
}

function parseDeclarations(body) {
  return splitTopLevel(body, ";")
    .map((decl) => {
      const colon = decl.indexOf(":");
      return colon < 0 ? null : [decl.slice(0, colon).trim().toLowerCase(), decl.slice(colon + 1).trim()];
    })
    .filter(Boolean);
}

// Style rules, descending into grouping at-rules and skipping @keyframes,
// @font-face and the like (their blocks are not element selectors).
function parseStyleRules(css) {
  const rules = [];
  const walk = (src) => {
    let i = 0;
    while (i < src.length) {
      const open = src.indexOf("{", i);
      if (open < 0) break;
      const prelude = src.slice(i, open).split(";").pop().trim();
      let depth = 1;
      let j = open + 1;
      while (j < src.length && depth > 0) {
        if (src[j] === "{") depth++;
        else if (src[j] === "}") depth--;
        j++;
      }
      const body = src.slice(open + 1, j - 1);
      if (prelude.startsWith("@")) {
        if (/^@(media|supports|layer|container)\b/i.test(prelude)) walk(body);
      } else {
        rules.push({ selectors: splitTopLevel(prelude, ","), declarations: parseDeclarations(body) });
      }
      i = j;
    }
  };
  walk(css.replace(/\/\*[\s\S]*?\*\//g, ""));
  return rules;
}

// The subject (rightmost compound) of a selector, as {tag, classes, ids}.
// Pseudo-class arguments are dropped, so `.a:nth-child(2)` matches every
// `.a`: a superset, which only ever makes the rules stricter.
function selectorSubject(selector) {
  const compound = selector.replace(/\([^)]*\)/g, "").split(/\s*[\s>+~]\s*/).filter(Boolean).pop() ?? "";
  const tag = /^[a-zA-Z][\w-]*/.exec(compound)?.[0]?.toLowerCase() ?? null;
  const classes = [...compound.matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
  const ids = [...compound.matchAll(/#([\w-]+)/g)].map((m) => m[1]);
  return { tag, classes, ids };
}

function subjectMatches(subject, element) {
  if (!subject.tag && subject.classes.length === 0 && subject.ids.length === 0) return false;
  if (subject.tag && subject.tag !== element.tag) return false;
  const classes = new Set(classesOf(element));
  if (!subject.classes.every((c) => classes.has(c))) return false;
  return subject.ids.every((id) => element.attrs.id === id);
}

const isAnimationDeclaration = ([prop, value]) =>
  (prop === "animation" || prop === "animation-name") && !/^none$/i.test(value.trim());

const TIME = /^(-?\d*\.?\d+)(m?s)$/i;
const toSeconds = (token) => {
  const m = TIME.exec(token.trim());
  if (!m) return null;
  return Number(m[1]) / (m[2].toLowerCase() === "ms" ? 1000 : 1);
};

// Custom properties visible to `element`: :root rules, then each ancestor's
// inline style down to the element's own (custom properties inherit).
function customProperties(element, rootRules) {
  const props = new Map();
  for (const rule of rootRules) {
    for (const [prop, value] of rule.declarations) if (prop.startsWith("--")) props.set(prop, value);
  }
  const chain = [];
  for (let el = element; el && el.attrs; el = el.parent) chain.unshift(el);
  for (const el of chain) {
    for (const [prop, value] of parseDeclarations(el.attrs.style ?? "")) {
      if (prop.startsWith("--")) props.set(prop, value);
    }
  }
  return props;
}

function resolveVars(value, props, depth = 0) {
  if (depth > 8) return value;
  return value.replace(/var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\))?[^()]*))?\)/g, (_, name, fallback) =>
    resolveVars(props.get(name) ?? fallback ?? "", props, depth + 1),
  );
}

// Every delay (seconds) a declaration list gives the element: animation-delay
// values, and the second time of each `animation` shorthand layer.
function delaysFrom(declarations, props) {
  const delays = [];
  for (const [prop, raw] of declarations) {
    const value = resolveVars(raw, props);
    if (prop === "animation-delay") {
      for (const part of splitTopLevel(value, ",")) {
        const s = toSeconds(part);
        if (s !== null) delays.push(s);
      }
    } else if (prop === "animation") {
      for (const layer of splitTopLevel(value, ",")) {
        const times = splitTopLevel(layer, " ").map(toSeconds).filter((s) => s !== null);
        if (times.length >= 2) delays.push(times[1]);
      }
    }
  }
  return delays;
}

// ---- The analysis of one cut

const PK_DELAY_PROPERTY = /^--pk-[\w-]*delay$/;

// kit.js turns the first time of data-waypoints / data-shots into the
// generated animation's delay.
function choreographyDelay(element) {
  const classes = classesOf(element);
  const raw = classes.includes("pk-cursor")
    ? element.attrs["data-waypoints"]
    : classes.includes("pk-camera")
      ? element.attrs["data-shots"]
      : undefined;
  if (raw === undefined) return [];
  const first = Number.parseFloat(raw.trim());
  return Number.isFinite(first) ? [first] : [];
}

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function typeTextTargetIds(scripts) {
  const all = scripts.join("\n");
  const ids = [];
  const unresolved = [];
  for (const m of all.matchAll(/\btypeText\(\s*([^,)]+)/g)) {
    const arg = m[1].trim();
    const direct = /^document\.getElementById\(\s*["']([^"']+)["']\s*\)$/.exec(arg);
    const viaVariable = /^[A-Za-z_$][\w$]*$/.test(arg)
      ? new RegExp(`\\b${escapeRegExp(arg)}\\s*=\\s*document\\.getElementById\\(\\s*["']([^"']+)["']\\s*\\)`).exec(all)
      : null;
    const id = direct?.[1] ?? viaVariable?.[1];
    if (id) ids.push(id);
    else unresolved.push(arg);
  }
  return { ids, unresolved };
}

/**
 * Analyse one cut. `readStylesheet(href)` returns a linked stylesheet's text,
 * or null when it does not exist.
 */
function analyseCut(html, readStylesheet) {
  const { elements, scripts, styles, links } = parseHtml(html);
  const css = [...links.map((href) => readStylesheet(href) ?? ""), ...styles].join("\n");
  const rules = parseStyleRules(css);
  const rootRules = rules.filter((rule) => rule.selectors.includes(":root"));
  const matchingRules = (element) =>
    rules.filter((rule) => rule.selectors.some((selector) => subjectMatches(selectorSubject(selector), element)));
  const typeTargets = typeTextTargetIds(scripts);

  const isAnimated = (element) => {
    const inline = parseDeclarations(element.attrs.style ?? "");
    if (inline.some(([prop, value]) => prop.startsWith("animation") && !/^none$/i.test(value))) return true;
    if (inline.some(([prop]) => PK_DELAY_PROPERTY.test(prop))) return true;
    if (matchingRules(element).some((rule) => rule.declarations.some(isAnimationDeclaration))) return true;
    if (element.attrs.id && typeTargets.ids.includes(element.attrs.id)) return true;
    const classes = classesOf(element);
    if (classes.includes("pk-cursor") && "data-waypoints" in element.attrs) return true;
    return classes.includes("pk-camera") && "data-shots" in element.attrs;
  };

  const startOf = (element) => {
    const value = Number.parseFloat(element.attrs["data-start"]);
    return Number.isFinite(value) ? value : Number.NaN;
  };

  const r1 = typeTargets.unresolved.map(
    (arg) => `typeText(${arg}, ...): cannot resolve the target statically; pass document.getElementById("...") or a variable assigned from it`,
  );
  for (const element of elements) {
    if ("data-start" in element.attrs || !isAnimated(element)) continue;
    let clock = element.parent;
    while (clock && clock.attrs && !("data-start" in clock.attrs)) clock = clock.parent;
    if (!clock || !clock.attrs) continue;
    const start = startOf(clock);
    if (start === 0) continue;
    r1.push(
      `${describeElement(element)} is animated but takes its clock from ${describeElement(clock)} ` +
        `data-start="${clock.attrs["data-start"]}"; give the clip data-start="0" and move the entrance into animation-delay (${RULE_POINTER})`,
    );
  }

  const r2 = [];
  for (const element of elements) {
    if (!("data-start" in element.attrs)) continue;
    const start = startOf(element);
    if (!(start > 0)) continue;
    const props = customProperties(element, rootRules);
    const inline = parseDeclarations(element.attrs.style ?? "");
    const delays = [
      ...delaysFrom(inline, props),
      ...inline.filter(([prop]) => PK_DELAY_PROPERTY.test(prop)).map(([, value]) => toSeconds(resolveVars(value, props))),
      ...matchingRules(element).flatMap((rule) => delaysFrom(rule.declarations, props)),
      ...choreographyDelay(element),
    ].filter((s) => s !== null);
    if (delays.some((delay) => Math.abs(delay - start) < 1e-6)) {
      r2.push(
        `${describeElement(element)} has data-start="${element.attrs["data-start"]}" and an animation-delay of ${start}s, ` +
          `so it animates at ${2 * start}s; keep one of the two (${RULE_POINTER})`,
      );
    }
  }
  return { r1, r2 };
}

// ---- Rule self-test on trap compositions

const fixtureStylesheet = (href) => {
  const path = resolve(here, "fixture", href);
  return existsSync(path) ? readFileSync(path, "utf8") : null;
};
const fixture = (body, head = "") => `<!doctype html>
<html lang="en">
  <head>
    <link rel="stylesheet" href="../../kit/kit.css" />
    ${head}
  </head>
  <body>
    <div id="stage" data-composition-id="fixture" data-width="1080" data-height="1920" data-start="0" data-duration="10">
      ${body}
    </div>
  </body>
</html>`;

const R1_TRAPS = [
  {
    name: "a beat with an inline animation-delay inside a frame clip at data-start 2",
    html: fixture(`<div class="pk-frame clip" data-start="2" data-duration="8"><div class="pk-card" style="animation-delay: 3s;">card</div></div>`),
  },
  {
    name: "a kit class with an animation (pk-caret) inside a clip at data-start 2",
    html: fixture(`<div class="clip" data-start="2" data-duration="8"><span class="pk-caret"></span></div>`),
  },
  {
    name: "a class animated by the cut's own <style> inside a clip at data-start 2",
    html: fixture(
      `<div class="clip" data-start="2" data-duration="8"><div class="trap-beat">beat</div></div>`,
      `<style>.trap-beat { opacity: 0; animation: pk-enter-fade 0.6s ease 4s forwards; }</style>`,
    ),
  },
  {
    name: "an inline --pk-*-delay inside a clip at data-start 6",
    html: fixture(`<div class="clip" data-start="6" data-duration="4"><div class="pk-cursor__ring" style="--pk-click-delay: 7.2s;"></div></div>`),
  },
  {
    name: "a typeText target inside a clip at data-start 0.2",
    html: fixture(
      `<div class="clip" data-start="0.2" data-duration="9.8"><span id="typed">hello</span></div>
      <script>var t = document.getElementById("typed"); window.PanelKit.typeText(t, { delay: 0.6 });</script>`,
    ),
  },
  {
    name: "a .pk-camera[data-shots] inside a clip at data-start 1",
    html: fixture(`<div class="clip" data-start="1" data-duration="9"><div class="pk-camera" data-shots="1 0 0 1; 5 0 0 1.05"></div></div>`),
  },
  {
    name: "a .pk-cursor[data-waypoints] inside a clip at data-start 1",
    html: fixture(`<div class="clip" data-start="1" data-duration="9"><div class="pk-cursor" data-waypoints="1 0 0; 3 10 10 click"></div></div>`),
  },
  {
    name: "a typeText call whose target cannot be resolved statically",
    html: fixture(`<span class="x">hello</span><script>window.PanelKit.typeText(document.querySelector(".x"), {});</script>`),
  },
];

const R2_TRAPS = [
  {
    name: "an inline animation-delay equal to the element's data-start",
    html: fixture(`<div class="pk-toast clip" data-start="2" data-duration="8" style="animation-delay: 2s;">toast</div>`),
  },
  {
    name: "a stylesheet animation-delay equal to the element's data-start",
    html: fixture(`<div class="pk-frame clip" data-start="2.6" data-duration="7.4"></div>`, `<style>.pk-frame { animation-delay: 2.6s; }</style>`),
  },
  {
    name: "an animation shorthand whose delay equals the element's data-start",
    html: fixture(
      `<p class="trap-line clip" data-start="0.8" data-duration="9.2">line</p>`,
      `<style>.trap-line { opacity: 0; animation: pk-enter-rise 0.9s cubic-bezier(0.22, 1, 0.36, 1) 0.8s forwards; }</style>`,
    ),
  },
  {
    name: "an inline --pk-*-delay feeding the kit animation, equal to data-start",
    html: fixture(`<span class="pk-pill pk-pill--swap-in clip" data-start="7.4" data-duration="2.6" style="--pk-pill-enter-delay: 7.4s;">done</span>`),
  },
  {
    name: "a .pk-cursor[data-waypoints] whose first waypoint equals its own data-start",
    html: fixture(`<div class="pk-cursor clip" data-start="4" data-duration="6" data-waypoints="4 0 0; 6 10 10 click"></div>`),
  },
];

const CLEAN = fixture(
  `<div class="pk-camera" data-shots="2.6 0 14 1.025; 6 0 0 1">
    <div class="pk-frame clip" data-start="0" data-duration="10">
      <div class="pk-card" style="animation-delay: 3s;">card</div>
      <span class="pk-caret"></span>
      <span id="typed">hello</span>
      <div class="pk-cursor__ring clip" data-start="6" data-duration="4" style="--pk-click-delay: 1.2s;"></div>
    </div>
  </div>
  <script>var t = document.getElementById("typed"); window.PanelKit.typeText(t, { delay: 0.6 });</script>`,
  `<style>.pk-frame { animation-delay: 2.6s; }</style>`,
);

describe("animation clock rules fire on trap compositions", () => {
  it("resolves the kit stylesheet the traps link", () => {
    expect(fixtureStylesheet("../../kit/kit.css")).not.toBeNull();
  });

  it.each(R1_TRAPS)("R1 fails $name", ({ html }) => {
    const { r1 } = analyseCut(html, fixtureStylesheet);
    expect(r1.length).toBeGreaterThan(0);
  });

  it.each(R2_TRAPS)("R2 fails $name", ({ html }) => {
    const { r2 } = analyseCut(html, fixtureStylesheet);
    expect(r2.length).toBeGreaterThan(0);
  });

  it("names the README rule in a failure", () => {
    const { r1 } = analyseCut(R1_TRAPS[0].html, fixtureStylesheet);
    expect(r1[0]).toContain(RULE_POINTER);
  });

  it("passes a composition that keeps every clock at 0 or on the element itself", () => {
    expect(analyseCut(CLEAN, fixtureStylesheet)).toEqual({ r1: [], r2: [] });
  });
});

// ---- The rules on every shipped cut

const cuts = readdirSync(here, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .flatMap((entry) =>
    CUT_FILES.filter((file) => existsSync(join(here, entry.name, file))).map((file) => ({ composition: entry.name, file })),
  );

const analyseShippedCut = ({ composition, file }) => {
  const dir = join(here, composition);
  return analyseCut(readFileSync(join(dir, file), "utf8"), (href) => {
    const path = resolve(dir, href);
    return existsSync(path) ? readFileSync(path, "utf8") : null;
  });
};

describe("animation clock on every cut", () => {
  it("finds the cuts", () => {
    expect(cuts.length).toBeGreaterThan(0);
  });

  it("allow-lists only compositions that exist", () => {
    const compositions = new Set(cuts.map((cut) => cut.composition));
    for (const id of R2_KEPT_AS_SHIPPED) expect(compositions.has(id)).toBe(true);
  });

  it.each(cuts)("R1: $composition/$file starts every animated element on one clock", (cut) => {
    expect(analyseShippedCut(cut).r1).toEqual([]);
  });

  it.each(cuts)("R2: $composition/$file never doubles a start", (cut) => {
    const { r2 } = analyseShippedCut(cut);
    if (R2_KEPT_AS_SHIPPED.has(cut.composition)) {
      // Still doubled as shipped; once repaired, drop it from R2_KEPT_AS_SHIPPED.
      expect(r2.length).toBeGreaterThan(0);
    } else {
      expect(r2).toEqual([]);
    }
  });
});
