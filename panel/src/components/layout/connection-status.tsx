"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Wifi, WifiOff, Loader2 } from "lucide-react";

type ConnectionState = "checking" | "connected" | "disconnected";

export function ConnectionStatus() {
  const [state, setState] = useState<ConnectionState>("checking");

  useEffect(() => {
    const checkConnection = async () => {
      try {
        // Health endpoint is at /health (not under /api)
        const response = await fetch("/health", {
          method: "GET",
          signal: AbortSignal.timeout(5000),
        });
        setState(response.ok ? "connected" : "disconnected");
      } catch {
        setState("disconnected");
      }
    };

    checkConnection();
    const interval = setInterval(checkConnection, 30000); // Check every 30s

    return () => clearInterval(interval);
  }, []);

  // Icon-only badge: the tooltip below is now the ONLY place the state is
  // spelled out in words, so each hint fully explains the state rather than
  // just naming it. role="status" + aria-label give the icon-only control
  // its accessible name (a visible label no longer exists to fall back on);
  // aria-live announces a state change to assistive tech without re-reading
  // on every unchanged 30s poll (aria-live only fires when content differs).
  const hint: Record<ConnectionState, string> = {
    checking: "Checking the orchestrator API...",
    connected: "Orchestrator API reachable, re-checked every 30s",
    disconnected: "Orchestrator API unreachable, retrying every 30s",
  };

  const badge =
    state === "checking" ? (
      <Badge
        variant="outline"
        className="gap-1"
        role="status"
        aria-live="polite"
        aria-label={hint.checking}
      >
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
      </Badge>
    ) : state === "connected" ? (
      <Badge
        variant="outline"
        className="gap-1 border-green-500 text-green-600"
        role="status"
        aria-live="polite"
        aria-label={hint.connected}
      >
        <Wifi className="h-3 w-3" aria-hidden="true" />
      </Badge>
    ) : (
      <Badge
        variant="outline"
        className="gap-1 border-orange-500 text-orange-600"
        role="status"
        aria-live="polite"
        aria-label={hint.disconnected}
      >
        <WifiOff className="h-3 w-3" aria-hidden="true" />
      </Badge>
    );

  return (
    <Tooltip>
      <TooltipTrigger asChild>{badge}</TooltipTrigger>
      <TooltipContent>{hint[state]}</TooltipContent>
    </Tooltip>
  );
}
