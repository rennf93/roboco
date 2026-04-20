"use client";

import Link from "next/link";
import { useStopAgent } from "@/hooks/use-agents";
import { AgentStatusResponse } from "@/types";
import { AgentDefinition } from "@/lib/agent-definitions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MoreHorizontal, Activity, Square } from "lucide-react";
import { toast } from "sonner";
import { AgentStateBadge } from "./agent-state-badge";
import { SpawnAgentDialog } from "./spawn-agent-dialog";

interface AgentCardProps {
  agent: AgentDefinition;
  agentStatus: AgentStatusResponse | null;
}

export function AgentCard({ agent, agentStatus }: AgentCardProps) {
  const stopAgent = useStopAgent();
  const state = agentStatus?.state || "stopped";
  const isActive = ["running", "ready", "starting", "waiting_long"].includes(state);

  const handleStop = async (graceful: boolean) => {
    try {
      await stopAgent.mutateAsync({ agentId: agent.id, graceful });
      const message = graceful ? "stopping gracefully" : "force stopped";
      toast.success("Agent " + agent.name + " " + message);
    } catch {
      toast.error("Failed to stop agent");
    }
  };

  return (
    <Card className={isActive ? "border-green-500/50" : ""}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{agent.name || "Unknown Agent"}</CardTitle>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {!isActive && (
                <SpawnAgentDialog agentId={agent.id} agentName={agent.name} />
              )}
              {isActive && (
                <>
                  <DropdownMenuItem asChild>
                    <Link href={"/agents/" + agent.id}>
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
        <CardDescription className="text-xs">
          {agent.role?.replace(/_/g, " ") || "N/A"}
          {agent.team && " • " + agent.team.replace(/_/g, " ")}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <AgentStateBadge state={state} />
        {agentStatus?.task_id && (
          <p className="text-xs text-muted-foreground mt-2">
            Task: {agentStatus.task_id.slice(0, 8)}...
          </p>
        )}
        {agentStatus?.waiting_for && (
          <p className="text-xs text-yellow-600 mt-2 truncate">
            Waiting: {agentStatus.waiting_for}
          </p>
        )}
        {agentStatus && agentStatus.error_count > 0 && (
          <p className="text-xs text-red-500 mt-2">
            Errors: {agentStatus.error_count}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
