"use client";

import { createContext } from "react";
import type { TelegramWebApp } from "@/lib/telegram/webapp";

/**
 * The bootstrapped Telegram WebApp bridge (real object or dev mock), or null
 * outside Telegram. Read it through `useTgWebApp` (`@/hooks/use-tg-webapp`).
 */
export const TgWebAppContext = createContext<TelegramWebApp | null>(null);

/**
 * Provides the bridge the Mini App page bootstrapped once, so components
 * reach native chrome (MainButton, BackButton) through the hooks in
 * `@/hooks/use-tg-webapp` and never touch `window.Telegram` directly.
 */
export function TgWebAppProvider({
  webApp,
  children,
}: {
  webApp: TelegramWebApp | null;
  children: React.ReactNode;
}) {
  return (
    <TgWebAppContext.Provider value={webApp}>
      {children}
    </TgWebAppContext.Provider>
  );
}
