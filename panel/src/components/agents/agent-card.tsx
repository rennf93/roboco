"use client";

import Link from "next/link";
import { useStopAgent } from "@/hooks/use-agents";
import { AgentStatusResponse } from "@/types";
import { AgentDefinition } from "@/lib/agent-definitions";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MoreHorizontal, Activity, Square } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { agentStateDescription, stateColors } from "./agent-state-badge";
import { SpawnAgentDialog } from "./spawn-agent-dialog";
import type { AgentUsageRow } from "@/types";

interface AgentCardProps {
  agent: AgentDefinition;
  agentStatus: AgentStatusResponse | null;
  usageRow?: AgentUsageRow | null;
}

export function AgentCard({ agent, agentStatus, usageRow }: AgentCardProps) {
  const stopAgent = useStopAgent();
  const state = agentStatus?.state || "stopped";
  // "Up" = anything that isn't a terminal/down state. Spawn is offered ONLY when
  // the agent is down; an up agent (active / running / idle / paused / …) shows
  // View Details + Stop instead. We list the DOWN states rather than the up ones
  // so a new "up" state the backend adds defaults to non-spawnable — the safe
  // side, since spawning an already-running agent is exactly the bug to avoid.
  // (The badge renders "active" as a first-class state, so it MUST count as up.)
  const isActive = !["stopped", "offline", "terminated", "error"].includes(
    state,
  );

  const handleStop = async (graceful: boolean) => {
    try {
      await stopAgent.mutateAsync({ agentId: agent.id, graceful });
      const message = graceful ? "stopping gracefully" : "force stopped";
      toast.success("Agent " + agent.name + " " + message);
    } catch {
      toast.error("Failed to stop agent");
    }
  };

  // One secondary line, priority error > waiting > task, so a card never
  // grows past two content rows regardless of how much is going on.
  const detail = agentStatus?.error_count
    ? {
        text:
          agentStatus.error_count +
          (agentStatus.error_count === 1 ? " error" : " errors"),
        className: "text-red-500",
      }
    : agentStatus?.waiting_for
      ? { text: "Waiting: " + agentStatus.waiting_for, className: "text-yellow-600" }
      : agentStatus?.task_id
        ? { text: "Task " + agentStatus.task_id.slice(0, 8) + "…", className: "text-muted-foreground" }
        : null;

  return (
    <Card className={cn("gap-2 py-3", isActive && "border-green-500/50")}>
      <CardHeader className="gap-0.5 px-3">
        <div className="flex items-center justify-between gap-1">
          <CardTitle className="truncate text-sm">
            {agent.name || "Unknown Agent"}
          </CardTitle>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 shrink-0"
                aria-label="Agent actions"
              >
                <MoreHorizontal className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {!isActive && (
                <SpawnAgentDialog agentId={agent.id} agentName={agent.name} />
              )}
              {isActive && (
                <>
                  <DropdownMenuItem asChild>
                    <Link href={"/agents/" + agent.id} prefetch={false}>
                      <Activity className="h-4 w-4 mr-2" />
                      View Details
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => handleStop(true)}>
                    <Square className="h-4 w-4 mr-2" />
                    Stop Gracefully
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => handleStop(false)}
                    className="text-red-600"
                  >
                    <Square className="h-4 w-4 mr-2" />
                    Force Stop
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <CardDescription className="truncate text-xs">
          {agent.role?.replace(/_/g, " ") || "N/A"}
          {agent.team && " • " + agent.team.replace(/_/g, " ")}
        </CardDescription>
      </CardHeader>
      <CardContent className="px-3">
        <HelpTip label={agentStateDescription(state)}>
          <span className="inline-flex items-center gap-1.5 text-xs font-medium">
            <span
              className={cn(
                "h-2 w-2 shrink-0 rounded-full",
                stateColors[state] || "bg-gray-400",
              )}
            />
            {state.replace(/_/g, " ")}
          </span>
        </HelpTip>
        {detail && (
          <p className={cn("mt-1 truncate text-xs", detail.className)}>
            {detail.text}
          </p>
        )}
        {usageRow && (
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {usageRow.total_tokens >= 1_000
              ? (usageRow.total_tokens / 1_000).toFixed(1) + "K"
              : String(usageRow.total_tokens)}{" "}
            tok · ${usageRow.cost_usd.toFixed(4)}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
