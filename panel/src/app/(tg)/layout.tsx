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
 */
export default function TelegramLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background text-foreground">
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
