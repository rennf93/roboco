import Script from "next/script";

/**
 * Slim shell for the Telegram Mini App surface (`/tg`) — no Sidebar/Header/
 * BottomTabBar, just a full-height scroll region. QueryClient/Theme/Toaster
 * already come from the root layout's <Providers>, so nothing new is
 * provided here.
 *
 * `beforeInteractive` is root-layout-only (Next.js throws outside
 * app/layout.tsx), so this loads the Telegram bridge script with the default
 * `afterInteractive` strategy instead — `waitForTelegramWebApp` (in
 * lib/telegram/webapp.ts) briefly polls for `window.Telegram.WebApp` to
 * absorb the resulting load race rather than assuming it's present on mount.
 *
 * Height reads `--tg-viewport-stable-height`, a :root variable the Telegram
 * script itself maintains (steady during keyboard/panel animations, unlike
 * dvh inside the webview); outside Telegram it's unset and 100dvh applies.
 * `#tg-shell` is the hook the page uses to scope Telegram theme variables
 * to this surface only.
 */
export default function TelegramLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div
      id="tg-shell"
      className="flex flex-col overflow-hidden bg-background text-foreground"
      style={{ height: "var(--tg-viewport-stable-height, 100dvh)" }}
    >
      <Script
        src="https://telegram.org/js/telegram-web-app.js"
        strategy="afterInteractive"
      />
      <main className="flex-1 overflow-auto pt-[env(safe-area-inset-top)]">
        {children}
      </main>
    </div>
  );
}
