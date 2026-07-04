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
import {
  fontFamily as displayFontFamily,
  loadFont as loadDisplayFont,
} from "@remotion/google-fonts/SpaceGrotesk";
import {
  fontFamily as bodyFontFamily,
  loadFont as loadBodyFont,
} from "@remotion/google-fonts/Inter";

// Registering the @font-face at module scope wraps a delayRender/
// continueRender internally (see Remotion's font-loading docs) — the
// renderer waits for these before it starts capturing frames, so no
// component needs to await anything itself.
loadDisplayFont("normal", { weights: ["500", "700"], subsets: ["latin"] });
loadBodyFont("normal", { weights: ["400", "500", "600"], subsets: ["latin"] });

export const font = {
  display: displayFontFamily,
  body: bodyFontFamily,
} as const;

export const color = {
  ink: "#0B0B10",
  inkRaised: "#15151C",
  paper: "#F5F2EA",
  muted: "#9C9891",
  accent: "#FF5A1F",
  hairline: "rgba(245, 242, 234, 0.12)",
} as const;
