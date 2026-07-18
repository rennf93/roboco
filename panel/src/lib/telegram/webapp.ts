/**
 * Telegram Mini App WebApp bridge.
 *
 * Thin wrapper over the global `window.Telegram.WebApp` object injected by
 * https://telegram.org/js/telegram-web-app.js (loaded by the `(tg)` layout).
 * Only the handful of fields/methods the cockpit actually needs are typed —
 * the real object carries far more (haptics, theme params, main button,
 * etc.) that nothing here uses yet.
 */

export interface TelegramWebApp {
  /** Signals the Mini App is ready to be displayed — hides Telegram's own
   * loading placeholder. Safe to call more than once. */
  ready: () => void;
  /** Expands the Mini App to full height (past the default half-screen). */
  expand: () => void;
  /** Opaque, HMAC-signed payload proving this session came from Telegram —
   * forwarded verbatim to `POST /api/telegram/webapp-auth`. Empty string
   * when the WebApp object exists but wasn't launched with real init data
   * (e.g. a bare browser tab pointed at the URL). */
  initData: string;
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

/** The live WebApp object, or null outside Telegram (or during SSR). */
export function getTelegramWebApp(): TelegramWebApp | null {
  if (typeof window === "undefined") return null;
  return window.Telegram?.WebApp ?? null;
}

/** Convenience accessor — "" when there's no WebApp (never null, so callers
 * don't need a separate not-in-Telegram branch just to read this). */
export function getInitData(): string {
  return getTelegramWebApp()?.initData ?? "";
}

const POLL_INTERVAL_MS = 100;

/**
 * Resolves the WebApp object, waiting briefly for the CDN script to finish
 * loading (it's fetched with `next/script`'s `afterInteractive` strategy, so
 * it can still be in flight when this runs on mount). Resolves null once
 * `timeoutMs` elapses with no `window.Telegram.WebApp` — the caller then
 * knows for certain this isn't a Telegram launch, not just a slow network.
 *
 * ponytail: a plain poll loop, not a script `onLoad` event — the script tag
 * lives in a layout the caller doesn't render, so there's no ref to hang a
 * listener off; polling a global is the shortest correct thing here.
 */
export function waitForTelegramWebApp(
  timeoutMs = 1500,
): Promise<TelegramWebApp | null> {
  const existing = getTelegramWebApp();
  if (existing) return Promise.resolve(existing);
  if (typeof window === "undefined") return Promise.resolve(null);

  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    const timer = setInterval(() => {
      const webApp = getTelegramWebApp();
      if (webApp) {
        clearInterval(timer);
        resolve(webApp);
      } else if (Date.now() >= deadline) {
        clearInterval(timer);
        resolve(null);
      }
    }, POLL_INTERVAL_MS);
  });
}
