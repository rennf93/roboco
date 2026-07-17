"use client";

import { useEffect, useState } from "react";
import api, { getErrorMessage } from "@/lib/api/client";
import { waitForTelegramWebApp } from "@/lib/telegram/webapp";
import { TgTabBar, type TgTab } from "@/components/tg/tg-tab-bar";
import { TgApprovalsTab } from "@/components/tg/tg-approvals-tab";
import { TgInboxTab } from "@/components/tg/tg-inbox-tab";
import { TgBoardTab } from "@/components/tg/tg-board-tab";
import { TgChatTab } from "@/components/tg/tg-chat-tab";
import { Loader2, AlertTriangle, ExternalLink } from "lucide-react";

type BootstrapState =
  | { kind: "validating" }
  | { kind: "ready" }
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
 */
export default function TelegramMiniAppPage() {
  const [state, setState] = useState<BootstrapState>({ kind: "validating" });
  const [tab, setTab] = useState<TgTab>("approvals");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const webApp = await waitForTelegramWebApp();
      if (cancelled) return;
      if (!webApp) {
        setState({ kind: "not_in_telegram" });
        return;
      }
      webApp.ready();
      webApp.expand();
      try {
        await api.post("/telegram/webapp-auth", {
          init_data: webApp.initData ?? "",
        });
        if (!cancelled) setState({ kind: "ready" });
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
    <div className="p-3 pb-20">
      {tab === "approvals" && <TgApprovalsTab />}
      {tab === "inbox" && <TgInboxTab />}
      {tab === "board" && <TgBoardTab />}
      {tab === "chat" && <TgChatTab />}
      <TgTabBar active={tab} onChange={setTab} />
    </div>
  );
}
