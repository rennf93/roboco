"use client";

import { useMemo } from "react";
import {
  useOrchestratorStatus,
  useWaitingAgents,
  useAgentDefinitions,
} from "@/hooks/use-agents";
import { useAgentUsage } from "@/hooks/use-usage";
import { AgentStatusResponse, AgentUsageRow } from "@/types";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";
import { OfflineState } from "@/components/ui/offline-state";
import {
  getBoardAgents,
  getMainPm,
  getBackendAgents,
  getFrontendAgents,
  getUxAgents,
  getOnDemandAgents,
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
  const { data: usageRows } = useAgentUsage();

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

  // Convert usage rows to a record keyed by agent_slug
  const agentUsageMap = useMemo(() => {
    const result: Record<string, AgentUsageRow> = {};
    for (const row of usageRows ?? []) {
      result[row.agent_slug] = row;
    }
    return result;
  }, [usageRows]);

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
        agentUsage={agentUsageMap}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={4}
      />

      <AgentGrid
        title="Main PM"
        agents={getMainPm(agents)}
        agentStatuses={agentStatuses}
        agentUsage={agentUsageMap}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={4}
      />

      <AgentGrid
        title="Backend Cell"
        agents={getBackendAgents(agents)}
        agentStatuses={agentStatuses}
        agentUsage={agentUsageMap}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={5}
      />

      <AgentGrid
        title="Frontend Cell"
        agents={getFrontendAgents(agents)}
        agentStatuses={agentStatuses}
        agentUsage={agentUsageMap}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={5}
      />

      <AgentGrid
        title="UX/UI Cell"
        agents={getUxAgents(agents)}
        agentStatuses={agentStatuses}
        agentUsage={agentUsageMap}
        isLoading={(isLoading || agentsLoading) && !isOffline}
        columns={4}
      />

      {/* On-Demand section: Prompter/Intake and Secretary agents — only rendered
          when the API returns at least one matching agent */}
      {getOnDemandAgents(agents).length > 0 && (
        <AgentGrid
          title="On-Demand"
          agents={getOnDemandAgents(agents)}
          agentStatuses={agentStatuses}
          agentUsage={agentUsageMap}
          isLoading={(isLoading || agentsLoading) && !isOffline}
          columns={4}
        />
      )}
    </div>
  );
}
