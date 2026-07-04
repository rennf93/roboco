/**
 * Shared visual language for motion/ compositions. New compositions should
 * import color + font from here instead of inventing their own — that's
 * what keeps the library coherent as more clips merge in over time.
 *
 * One accent color, a near-black (never pure #000) ink field, warm
 * off-white (never pure #fff) text. A display face for headlines paired
 * with a workhorse body face for everything else — see motion/README.md
 * for the full design-bar rationale.
 */
import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

export const font = {
  display: "Share Tech Mono",
  body: "Inter",
} as const;

// Vendored under public/fonts/ (see motion/README.md) instead of fetched
// from Google's font CDN at render time via the old Google Fonts
// integration package: that fetch happens inside Chrome, raced against an
// 18s timeout, so a slow or unreachable network is a render failure — and
// one this sidecar has no business depending on at all. Inter is the same
// variable-font instance Google itself serves for these weights; Share
// Tech Mono ships only one static weight (400 Regular — confirmed via its
// own name/OS2 tables: no bold, no italic, no variable axes), so only that
// weight is loaded below.
const displayFontUrl = staticFile("fonts/ShareTechMono-Regular.woff2");
const bodyFontUrl = staticFile("fonts/Inter-Variable.woff2");

// loadFont() wraps a delayRender/continueRender internally — the renderer
// waits for these before it starts capturing frames, so no component
// needs to await anything itself. Unlike the old Google Fonts integration
// package, @remotion/fonts has no built-in guard for a missing `FontFace`
// global; skip explicitly rather than let it hit cancelRender() in the
// jsdom test environment, where there is no real render to cancel.
const loadWeights = (family: string, url: string, weights: string[]) => {
  if (typeof FontFace === "undefined") {
    return;
  }
  for (const weight of weights) {
    loadFont({ family, url, weight, style: "normal" });
  }
};

loadWeights(font.display, displayFontUrl, ["400"]);
loadWeights(font.body, bodyFontUrl, ["400", "500", "600"]);

export const color = {
  ink: "#0B0B10",
  inkRaised: "#15151C",
  paper: "#F5F2EA",
  muted: "#9C9891",
  accent: "#FF5A1F",
  hairline: "rgba(245, 242, 234, 0.12)",
} as const;
