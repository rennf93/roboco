"use client";

import { Loader2, WifiOff, X } from "lucide-react";
import type { ConnectionState } from "@/lib/websocket/connection";
import { cn } from "@/lib/utils";
import { HelpTip } from "@/components/ui/help-tip";
import { connectionDotClasses, connectionStateLabel } from "./a2a-utils";

/** Pane-header connection indicator: dot + label, plus a spinner/offline icon
 * for the connecting/reconnecting/disconnected states (design doc §3). All
 * four `ConnectionState` values render distinctly — a live-but-quiet
 * conversation must read differently from a stream that is the problem. */
export function A2AConnectionBadge({ state }: { state: ConnectionState }) {
  return (
    <div className="flex items-center gap-1.5">
      <span
        className={cn("h-2 w-2 rounded-full", connectionDotClasses(state))}
      />
      <span className="text-xs text-muted-foreground">
        {connectionStateLabel(state)}
      </span>
      {(state === "connecting" || state === "reconnecting") && (
        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
      )}
      {state === "disconnected" && (
        <WifiOff className="h-3 w-3 text-muted-foreground" />
      )}
    </div>
  );
}

interface A2AConnectionBannerProps {
  state: "reconnecting" | "disconnected";
  onDismiss: () => void;
}

/** Dismissable strip above the stream pane's message list — a scoped
 * live-connection hint, not the full-page `OfflineState` (design doc §3). */
export function A2AConnectionBanner({
  state,
  onDismiss,
}: A2AConnectionBannerProps) {
  const isDisconnected = state === "disconnected";
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-2 border-b text-xs px-3 py-1.5",
        isDisconnected
          ? "bg-destructive/10 border-destructive/30 text-destructive"
          : "bg-amber-500/10 border-amber-500/30 text-amber-700 dark:text-amber-400",
      )}
    >
      <span>
        {isDisconnected
          ? "Disconnected — reconnecting automatically"
          : "Reconnecting — messages may be out of date"}
      </span>
      <HelpTip label="Dismiss">
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          title="Dismiss"
          className="shrink-0 opacity-70 hover:opacity-100"
        >
          <X className="h-3 w-3" />
        </button>
      </HelpTip>
    </div>
  );
}
