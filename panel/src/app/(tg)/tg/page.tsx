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
import { TgMetricsTab } from "@/components/tg/tg-metrics-tab";
import { TgSubPage, TG_PRESS } from "@/components/tg/ui";
import { IconInbox } from "@/components/tg/tg-icons";
import { isTgDemoMode } from "@/lib/telegram/demo";
import { useNotifications } from "@/hooks/use-notifications";
import { cn } from "@/lib/utils";
import { IconContext } from "@phosphor-icons/react";
import {
  ArrowSquareOut,
  CircleNotch,
  Warning,
} from "@phosphor-icons/react";

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
      // No bridge, or a bridge with empty initData that didn't become the
      // dev mock (a plain browser at this URL in production) — show the
      // "Open from Telegram" wall instead of posting an empty payload that
      // 422s into a "Couldn't sign in" error.
      if (!webApp || (!webApp.initData && !isDevMockWebApp(webApp))) {
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
        <CircleNotch weight="bold" className="h-8 w-8 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Connecting…</p>
      </CenteredMessage>
    );
  }

  if (state.kind === "not_in_telegram") {
    return (
      <CenteredMessage>
        <ArrowSquareOut weight="duotone" className="h-10 w-10 text-muted-foreground" />
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
        <Warning weight="duotone" className="h-10 w-10 text-destructive" />
        <h1 className="text-lg font-semibold">Couldn&apos;t sign in</h1>
        <p className="text-sm text-muted-foreground">{state.message}</p>
      </CenteredMessage>
    );
  }

  return (
    <TgWebAppProvider webApp={state.webApp}>
      <CockpitShell />
    </TgWebAppProvider>
  );
}

/**
 * The signed-in cockpit: brand header (wordmark + inbox bell), the active
 * tab, and the floating dock. Lives below the auth gate so its data hooks
 * only ever fire with a valid session.
 */
function CockpitShell() {
  const [tab, setTab] = useState<TgTab>("today");
  // Today's Ship action deep-focuses the release proposal in Approvals.
  const [approvalsFocus, setApprovalsFocus] = useState<"release" | undefined>();
  // Inbox is a pushed sub-page behind the header bell, not a tab.
  const [inboxOpen, setInboxOpen] = useState(false);
  const navigate = (next: TgTab, intent?: "release") => {
    setApprovalsFocus(next === "approvals" ? intent : undefined);
    setInboxOpen(false);
    setTab(next);
  };
  const { data: notifications } = useNotifications();
  // The live query doesn't run against fixtures, so demo shows a static
  // badge rather than an empty bell.
  const unread = isTgDemoMode() ? 3 : (notifications?.unread_count ?? 0);

  return (
    // Every Phosphor glyph inside the cockpit is duotone unless a wrapper
    // pins an explicit weight (the dock's filled active state).
    <IconContext.Provider value={{ weight: "duotone" }}>
      <div className="p-3 pb-28">
        <header className="flex items-center justify-between px-1 pb-3 pt-1">
        <span className="tg-brand text-[13px] tracking-[0.3em] text-foreground">
          ROBOCO<span className="tg-cursor text-primary">_</span>
        </span>
        <button
          type="button"
          aria-label={unread > 0 ? `Inbox, ${unread} unread` : "Inbox"}
          onClick={() => setInboxOpen(true)}
          className={cn(
            "relative flex h-10 w-10 items-center justify-center rounded-full bg-card text-muted-foreground",
            TG_PRESS,
          )}
        >
          <IconInbox className="h-5 w-5" />
          {unread > 0 && (
            <span className="absolute right-1 top-1 flex h-2 w-2 rounded-full bg-primary" />
          )}
        </button>
      </header>
        {inboxOpen ? (
          <TgSubPage title="Inbox" onBack={() => setInboxOpen(false)}>
            <TgInboxTab />
          </TgSubPage>
        ) : (
          // Keyed by tab so every switch replays the rise-in entrance.
          <div key={tab} className="tg-tab-in">
            {tab === "today" && <TgTodayTab onNavigate={navigate} />}
            {tab === "approvals" && (
              <TgApprovalsTab initialFocus={approvalsFocus} />
            )}
            {tab === "board" && <TgBoardTab />}
            {tab === "chat" && <TgChatTab />}
            {tab === "metrics" && <TgMetricsTab />}
          </div>
        )}
        <TgTabBar active={tab} onChange={navigate} />
      </div>
    </IconContext.Provider>
  );
}
