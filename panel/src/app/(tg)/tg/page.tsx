"use client";

import { useEffect, useState } from "react";
import api, { getErrorMessage } from "@/lib/api/client";
import {
  createDevMockWebApp,
  isDevMockWebApp,
  waitForTelegramWebApp,
  type TelegramWebApp,
} from "@/lib/telegram/webapp";
import { startTelegramThemeSync } from "@/lib/telegram/theme";
import { TgWebAppProvider } from "@/lib/telegram/hooks";
import { TgTabBar, type TgTab } from "@/components/tg/tg-tab-bar";
import { TgTodayTab } from "@/components/tg/tg-today-tab";
import { TgApprovalsTab } from "@/components/tg/tg-approvals-tab";
import { TgInboxTab } from "@/components/tg/tg-inbox-tab";
import { TgBoardTab } from "@/components/tg/tg-board-tab";
import { TgChatTab } from "@/components/tg/tg-chat-tab";
import { Loader2, AlertTriangle, ExternalLink } from "lucide-react";

type BootstrapState =
  | { kind: "validating" }
  | { kind: "ready"; webApp: TelegramWebApp }
  | { kind: "not_in_telegram" }
  | { kind: "error"; message: string };

function CenteredMessage({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-[70dvh] flex-col items-center justify-center gap-3 p-6 text-center">
      {children}
    </div>
  );
}

/**
 * `/tg` — the CEO's phone cockpit. On mount: resolve the Telegram WebApp
 * bridge, then POST its initData to the auth route unconditionally (the
 * route is idempotent — it just re-mints the session cookie on every call)
 * before rendering the tabbed cockpit. There's no way to read the resulting
 * httponly session cookie client-side to skip this on a warm reload, so it
 * always runs; it's cheap and the backend contract says so explicitly.
 *
 * Outside Telegram, a development build falls back to the dev mock bridge
 * (skipping the auth POST — the regular panel session cookie authorizes the
 * API calls) so the shell is workable in a plain browser; production keeps
 * the "Open from Telegram" wall.
 */
export default function TelegramMiniAppPage() {
  const [state, setState] = useState<BootstrapState>({ kind: "validating" });
  const [tab, setTab] = useState<TgTab>("today");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let webApp = await waitForTelegramWebApp();
      if (cancelled) return;
      // Outside Telegram the CDN script still loads and defines a bridge
      // object — just with empty initData (a real launch always carries
      // it). Either shape means "not a Telegram launch" for the dev
      // fallback.
      if (!webApp?.initData && process.env.NODE_ENV === "development") {
        webApp = createDevMockWebApp();
      }
      if (!webApp) {
        setState({ kind: "not_in_telegram" });
        return;
      }
      webApp.ready();
      webApp.expand();
      webApp.disableVerticalSwipes?.();
      if (isDevMockWebApp(webApp)) {
        setState({ kind: "ready", webApp });
        return;
      }
      try {
        await api.post("/telegram/webapp-auth", {
          init_data: webApp.initData ?? "",
        });
        if (!cancelled) setState({ kind: "ready", webApp });
      } catch (err) {
        if (!cancelled) {
          setState({ kind: "error", message: getErrorMessage(err) });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Adopt the user's Telegram palette for the whole shell (and track live
  // theme switches). Scoped to #tg-shell so the desktop dashboard is
  // untouched; the dev mock carries empty themeParams, so a dev browser
  // keeps the panel's own theme.
  useEffect(() => {
    if (state.kind !== "ready") return;
    const shell = document.getElementById("tg-shell");
    if (!shell) return;
    return startTelegramThemeSync(state.webApp, shell);
  }, [state]);

  if (state.kind === "validating") {
    return (
      <CenteredMessage>
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Connecting…</p>
      </CenteredMessage>
    );
  }

  if (state.kind === "not_in_telegram") {
    return (
      <CenteredMessage>
        <ExternalLink className="h-10 w-10 text-muted-foreground" />
        <h1 className="text-lg font-semibold">Open from Telegram</h1>
        <p className="text-sm text-muted-foreground">
          This cockpit only runs inside Telegram. Open it from the bot&apos;s
          menu button.
        </p>
      </CenteredMessage>
    );
  }

  if (state.kind === "error") {
    return (
      <CenteredMessage>
        <AlertTriangle className="h-10 w-10 text-destructive" />
        <h1 className="text-lg font-semibold">Couldn&apos;t sign in</h1>
        <p className="text-sm text-muted-foreground">{state.message}</p>
      </CenteredMessage>
    );
  }

  return (
    <TgWebAppProvider webApp={state.webApp}>
      <div className="p-3 pb-20">
        {tab === "today" && <TgTodayTab onNavigate={setTab} />}
        {tab === "approvals" && <TgApprovalsTab />}
        {tab === "inbox" && <TgInboxTab />}
        {tab === "board" && <TgBoardTab />}
        {tab === "chat" && <TgChatTab />}
        <TgTabBar active={tab} onChange={setTab} />
      </div>
    </TgWebAppProvider>
  );
}
