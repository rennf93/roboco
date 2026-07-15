"use client";

import { useEffect, useRef } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { HelpTip } from "@/components/ui/help-tip";
import { useAgentStream, ConnectionState } from "@/hooks/use-websocket";
import { Wifi, WifiOff, Loader2, Trash2 } from "lucide-react";

interface AgentStreamViewerProps {
  agentId: string;
  agentName?: string;
}

const stateColors: Record<ConnectionState, string> = {
  connected: "bg-green-500",
  connecting: "bg-yellow-500",
  reconnecting: "bg-orange-500",
  disconnected: "bg-gray-500",
};

const stateLabels: Record<ConnectionState, string> = {
  connected: "Connected",
  connecting: "Connecting...",
  reconnecting: "Reconnecting...",
  disconnected: "Disconnected",
};

export function AgentStreamViewer({
  agentId,
  agentName,
}: AgentStreamViewerProps) {
  const {
    state,
    streamOutput,
    streamChunks,
    clearMessages,
    isConnected,
    isConnecting,
  } = useAgentStream(agentId);

  const outputRef = useRef<HTMLPreElement>(null);

  // Auto-scroll to bottom when new content arrives
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [streamOutput]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              Agent Output Stream
              {isConnecting ? (
                <Loader2 className="h-4 w-4 animate-spin text-yellow-500" />
              ) : isConnected ? (
                <Wifi className="h-4 w-4 text-green-500" />
              ) : (
                <WifiOff className="h-4 w-4 text-gray-500" />
              )}
            </CardTitle>
            <CardDescription>
              Real-time LLM output from {agentName || agentId}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <HelpTip label="Live WebSocket connection carrying this agent's output — auto-reconnects on drop">
              <Badge className={stateColors[state] + " text-white"}>
                {stateLabels[state]}
              </Badge>
            </HelpTip>
            {streamChunks.length > 0 && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={clearMessages}
                    aria-label="Clear stream output"
                    title="Clear stream output"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Clear stream output</TooltipContent>
              </Tooltip>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <pre
          ref={outputRef}
          className="bg-slate-950 text-slate-50 rounded-lg p-4 h-96 overflow-auto font-mono text-sm whitespace-pre-wrap"
        >
          {streamOutput || (
            <span className="text-slate-500">
              {isConnected
                ? "Waiting for agent output..."
                : isConnecting
                  ? "Connecting to agent stream..."
                  : "Agent stream disconnected"}
            </span>
          )}
        </pre>
        <div className="flex justify-between items-center mt-2 text-sm text-muted-foreground">
          <HelpTip label="Number of stream messages received over this WebSocket connection">
            <span className="w-fit">{streamChunks.length} chunks received</span>
          </HelpTip>
          <HelpTip label="Total length of the accumulated output text below">
            <span className="w-fit">{streamOutput.length} characters</span>
          </HelpTip>
        </div>
      </CardContent>
    </Card>
  );
}
