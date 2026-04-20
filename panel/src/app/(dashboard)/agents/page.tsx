"use client";

import { useMemo } from "react";
import {
  useOrchestratorStatus,
  useWaitingAgents,
  useAgentDefinitions,
} from "@/hooks/use-agents";
import { AgentStatusResponse } from "@/types";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";
import { OfflineState } from "@/components/ui/offline-state";
import {
  getBoardAgents,
  getMainPm,
  getBackendAgents,
  getFrontendAgents,
  getUxAgents,
} from "@/lib/agent-definitions";
import {
  OrchestratorStatusCards,
  WaitingAgentsAlert,
  AgentGrid,
} from "@/components/agents";

export default function AgentsPage() {
  const { data: agents = [], isLoading: agentsLoading } = useAgentDefinitions();
  const { data: status, isLoading, error, refetch } = useOrchestratorStatus();
  const { data: waitingAgents } = useWaitingAgents();

  // Check if it's a connection error (backend not running)
  const isOffline = error && (
    error.message?.includes("Network Error") ||
    error.message?.includes("ECONNREFUSED") ||
    (error as { code?: string })?.code === "ERR_NETWORK"
  );

  // Convert agents array to a record keyed by agent_id for easy lookup
  const agentStatuses = useMemo(() => {
    const result: Record<string, AgentStatusResponse> = {};
    if (status?.agents) {
      for (const agent of status.agents) {
        result[agent.agent_id] = agent;
      }
    }
    return result;
  }, [status]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Agents</h1>
          <p className="text-muted-foreground">
            Monitor and control your AI workforce
          </p>
        </div>
        <Button variant="outline" onClick={() => refetch()}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      {isOffline ? (
        <OfflineState
          title="Orchestrator Not Running"
          description="Start the RoboCo orchestrator to spawn and monitor agents. The agent roster is shown below for reference."
          onRetry={() => refetch()}
        />
      ) : (
        <>
          {/* Status Overview */}
          <OrchestratorStatusCards status={status} isLoading={isLoading} />

          {/* Waiting Agents Alert */}
          {waitingAgents && <WaitingAgentsAlert waitingAgents={waitingAgents} />}
        </>
      )}

      {/* Agent Grids - Dynamically loaded from API */}
      <AgentGrid
        title="Board"
        agents={getBoardAgents(agents)}
        agentStatuses={agentStatuses}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={4}
      />

      <AgentGrid
        title="Main PM"
        agents={getMainPm(agents)}
        agentStatuses={agentStatuses}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={4}
      />

      <AgentGrid
        title="Backend Cell"
        agents={getBackendAgents(agents)}
        agentStatuses={agentStatuses}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={5}
      />

      <AgentGrid
        title="Frontend Cell"
        agents={getFrontendAgents(agents)}
        agentStatuses={agentStatuses}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={5}
      />

      <AgentGrid
        title="UX/UI Cell"
        agents={getUxAgents(agents)}
        agentStatuses={agentStatuses}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={4}
      />
    </div>
  );
}
