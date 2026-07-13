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

  const badge =
    state === "checking" ? (
      <Badge variant="outline" className="gap-1">
        <Loader2 className="h-3 w-3 animate-spin" />
        Checking...
      </Badge>
    ) : state === "connected" ? (
      <Badge
        variant="outline"
        className="gap-1 border-green-500 text-green-600"
      >
        <Wifi className="h-3 w-3" />
        Connected
      </Badge>
    ) : (
      <Badge
        variant="outline"
        className="gap-1 border-orange-500 text-orange-600"
      >
        <WifiOff className="h-3 w-3" />
        Offline
      </Badge>
    );

  const hint: Record<ConnectionState, string> = {
    checking: "Checking the orchestrator API…",
    connected: "Orchestrator API reachable — re-checked every 30s",
    disconnected: "Orchestrator API unreachable — retrying every 30s",
  };

  return (
    <Tooltip>
      <TooltipTrigger asChild>{badge}</TooltipTrigger>
      <TooltipContent>{hint[state]}</TooltipContent>
    </Tooltip>
  );
}
