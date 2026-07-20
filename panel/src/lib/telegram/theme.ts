/**
 * Telegram theme → panel token bridge.
 *
 * Maps the launcher's `themeParams` colors onto the shadcn CSS variables the
 * whole component library reads, scoped to the `(tg)` shell element only —
 * the desktop dashboard keeps its own theme. Later map entries win by
 * re-setting the same variable, so a newer, more specific Telegram key
 * (e.g. `section_bg_color`) overrides the broader fallback before it.
 */

import type { TelegramThemeParams, TelegramWebApp } from "./webapp";

/** [Telegram key, panel CSS variable] — applied in order. Surfaces only:
 * the accent tokens (--primary/--ring) deliberately stay RoboCo's amber
 * (the #tg-shell skin in globals.css) so the cockpit keeps its identity
 * inside any Telegram theme — Telegram's surfaces, RoboCo's voice. */
const THEME_VAR_MAP: ReadonlyArray<[keyof TelegramThemeParams, string]> = [
  ["bg_color", "--background"],
  ["secondary_bg_color", "--background"],
  ["bg_color", "--card"],
  ["section_bg_color", "--card"],
  ["bg_color", "--popover"],
  ["section_bg_color", "--popover"],
  ["text_color", "--foreground"],
  ["text_color", "--card-foreground"],
  ["text_color", "--popover-foreground"],
  ["hint_color", "--muted-foreground"],
  ["subtitle_text_color", "--muted-foreground"],
  ["destructive_text_color", "--destructive"],
  ["section_separator_color", "--border"],
];

/** Telegram promises `#rrggbb`; anything else is dropped rather than
 * injected into a style attribute (the bridge object is still a trust
 * boundary — a malformed value must not become CSS). */
const HEX_COLOR = /^#[0-9a-f]{6}$/i;

/**
 * Applies the WebApp's current colorScheme + themeParams to `root`: toggles
 * the `.dark` class (so unmapped tokens and `dark:` variants stay coherent)
 * and sets every validly-colored mapped variable inline (inline wins over
 * both `:root` and `.dark` definitions). Missing/invalid params are simply
 * skipped — the panel's own theme shows through, which is the right
 * degraded look.
 */
/** The #tg-shell default background as hex — what Telegram's own window
 * chrome is painted with when the theme doesn't hand us a bg_color.
 * Keep in step with `--background` in globals.css' #tg-shell block. */
const SHELL_BG_HEX = "#14171c";

export function applyTelegramTheme(
  webApp: TelegramWebApp,
  root: HTMLElement,
): void {
  root.classList.toggle("dark", webApp.colorScheme === "dark");
  const params = webApp.themeParams ?? {};
  for (const [key, cssVar] of THEME_VAR_MAP) {
    const value = params[key];
    if (value && HEX_COLOR.test(value)) {
      root.style.setProperty(cssVar, value);
    }
  }
  // Paint Telegram's own window chrome (titlebar / app bg / bottom bar) to
  // the shell background so the cockpit blends edge-to-edge into the client
  // instead of sitting framed inside default chrome — the single biggest
  // "native app, not website" tell.
  const bg =
    params.bg_color && HEX_COLOR.test(params.bg_color)
      ? params.bg_color
      : SHELL_BG_HEX;
  webApp.setHeaderColor?.(bg);
  webApp.setBackgroundColor?.(bg);
  webApp.setBottomBarColor?.(bg);
}

/**
 * Applies the theme now and re-applies on every `themeChanged` bridge event
 * (the user switching Telegram themes mid-session). Returns a cleanup that
 * unsubscribes; safe when the bridge lacks onEvent/offEvent (older clients,
 * the dev mock) — then it's a one-shot apply with a no-op cleanup.
 */
export function startTelegramThemeSync(
  webApp: TelegramWebApp,
  root: HTMLElement,
): () => void {
  applyTelegramTheme(webApp, root);
  if (!webApp.onEvent || !webApp.offEvent) return () => undefined;
  const handler = () => applyTelegramTheme(webApp, root);
  webApp.onEvent("themeChanged", handler);
  return () => webApp.offEvent?.("themeChanged", handler);
}
