"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Wifi, WifiOff, Loader2 } from "lucide-react";

type ConnectionState = "checking" | "connected" | "disconnected";

export function ConnectionStatus() {
  const [state, setState] = useState<ConnectionState>("checking");

  useEffect(() => {
    const checkConnection = async () => {
      try {
        // Health endpoint is at /health (not under /api/v1)
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

  if (state === "checking") {
    return (
      <Badge variant="outline" className="gap-1">
        <Loader2 className="h-3 w-3 animate-spin" />
        Checking...
      </Badge>
    );
  }

  if (state === "connected") {
    return (
      <Badge variant="outline" className="gap-1 border-green-500 text-green-600">
        <Wifi className="h-3 w-3" />
        Connected
      </Badge>
    );
  }

  return (
    <Badge variant="outline" className="gap-1 border-orange-500 text-orange-600">
      <WifiOff className="h-3 w-3" />
      Offline
    </Badge>
  );
}
