/**
 * Telegram Mini App WebApp bridge.
 *
 * Thin wrapper over the global `window.Telegram.WebApp` object injected by
 * https://telegram.org/js/telegram-web-app.js (loaded by the `(tg)` layout).
 * Only the fields/methods the cockpit actually uses are typed — the real
 * object carries far more.
 */

/** Colors Telegram derives from the user's active Telegram theme. All hex
 * (`#rrggbb`), all optional — older clients omit the newer keys. */
export interface TelegramThemeParams {
  bg_color?: string;
  secondary_bg_color?: string;
  section_bg_color?: string;
  section_separator_color?: string;
  text_color?: string;
  hint_color?: string;
  subtitle_text_color?: string;
  link_color?: string;
  accent_text_color?: string;
  button_color?: string;
  button_text_color?: string;
  destructive_text_color?: string;
}

export interface TelegramMainButton {
  setText: (text: string) => void;
  show: () => void;
  hide: () => void;
  enable: () => void;
  disable: () => void;
  showProgress: (leaveActive?: boolean) => void;
  hideProgress: () => void;
  onClick: (cb: () => void) => void;
  offClick: (cb: () => void) => void;
}

export interface TelegramBackButton {
  show: () => void;
  hide: () => void;
  onClick: (cb: () => void) => void;
  offClick: (cb: () => void) => void;
}

export interface TelegramHapticFeedback {
  impactOccurred: (
    style: "light" | "medium" | "heavy" | "rigid" | "soft",
  ) => void;
  notificationOccurred: (type: "error" | "success" | "warning") => void;
  selectionChanged: () => void;
}

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
  /** "light" | "dark" — tracks the user's Telegram theme. */
  colorScheme?: "light" | "dark";
  themeParams?: TelegramThemeParams;
  /** Subscribe/unsubscribe to bridge events ("themeChanged", …). */
  onEvent?: (event: string, cb: () => void) => void;
  offEvent?: (event: string, cb: () => void) => void;
  /** Bot API 7.7+ — stops vertical swipes from minimizing the app so
   * scrolling a list never accidentally dismisses the cockpit. */
  disableVerticalSwipes?: () => void;
  HapticFeedback?: TelegramHapticFeedback;
  MainButton?: TelegramMainButton;
  BackButton?: TelegramBackButton;
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

const DEV_MOCK_MARKER = "__robocoDevMock";

/**
 * Dev-only stand-in for the real WebApp object so the cockpit shell renders
 * in a plain desktop browser (`pnpm dev` + a normal panel session). Every
 * bridge method is a no-op; `initData` is empty and the page skips the
 * webapp-auth POST for a mock (see `isDevMockWebApp`), riding the regular
 * session cookie instead. The caller gates on NODE_ENV === "development",
 * so production builds eliminate the branch entirely.
 */
export function createDevMockWebApp(): TelegramWebApp {
  const noop = () => undefined;
  const mock: TelegramWebApp & Record<string, unknown> = {
    ready: noop,
    expand: noop,
    initData: "",
    colorScheme: window.matchMedia?.("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light",
    themeParams: {},
    [DEV_MOCK_MARKER]: true,
  };
  return mock;
}

/** True for objects minted by `createDevMockWebApp` — never for the real
 * bridge, whose surface Telegram controls. */
export function isDevMockWebApp(webApp: TelegramWebApp): boolean {
  return DEV_MOCK_MARKER in webApp;
}

/** Null-safe haptic feedback — silently no-ops outside Telegram (and in the
 * dev mock, which has no HapticFeedback object). */
export const haptics = {
  /** Light tap for selections/navigation. */
  tap(): void {
    getTelegramWebApp()?.HapticFeedback?.impactOccurred("light");
  },
  /** Success notification pulse after a mutation lands. */
  success(): void {
    getTelegramWebApp()?.HapticFeedback?.notificationOccurred("success");
  },
  /** Error notification pulse after a mutation fails. */
  error(): void {
    getTelegramWebApp()?.HapticFeedback?.notificationOccurred("error");
  },
};

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
