"use client";

import { useEffect, useMemo } from "react";
import {
  useOrchestratorStatus,
  useWaitingAgents,
  useAgentDefinitions,
} from "@/hooks/use-agents";
import { useAgentUsage } from "@/hooks/use-usage";
import { AgentStatusResponse, AgentUsageRow } from "@/types";
import { OfflineState } from "@/components/ui/offline-state";
import { usePageRefresh } from "@/hooks";
import {
  getBoardAgents,
  getMainPm,
  getBackendAgents,
  getFrontendAgents,
  getUxAgents,
  getSupportAgents,
} from "@/lib/agent-definitions";
import {
  OrchestratorStatusCards,
  WaitingAgentsAlert,
  AgentGrid,
} from "@/components/agents";

/** Fleet tab content — extracted from the standalone /agents page so it can
 * live inside the Agents hub tab shell (see agents/page.tsx). */
export function AgentsFleetView() {
  const { data: agents = [], isLoading: agentsLoading } = useAgentDefinitions();
  const { data: status, isLoading, error, refetch } = useOrchestratorStatus();
  const { data: waitingAgents } = useWaitingAgents();
  const { data: usageRows } = useAgentUsage();

  const { register, unregister, refresh } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  // Check if it's a connection error (backend not running)
  const isOffline =
    error &&
    (error.message?.includes("Network Error") ||
      error.message?.includes("ECONNREFUSED") ||
      (error as { code?: string })?.code === "ERR_NETWORK");

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
      </div>

      {isOffline ? (
        <OfflineState
          title="Orchestrator Not Running"
          description="Start the RoboCo orchestrator to spawn and monitor agents. The agent roster is shown below for reference."
          onRetry={() => void refresh()}
        />
      ) : (
        <>
          {/* Status Overview — Total Agents is the full roster size, not the
              orchestrator's live-instance count, so it stays truthful even
              when most of the roster isn't currently spawned. */}
          <OrchestratorStatusCards
            status={status}
            isLoading={isLoading}
            rosterCount={agents.length}
            rosterLoading={agentsLoading}
          />

          {/* Waiting Agents Alert */}
          {waitingAgents && (
            <WaitingAgentsAlert waitingAgents={waitingAgents} />
          )}
        </>
      )}

      {/* Agent Grids - Dynamically loaded from API. Board + Main PM fold into
          one Leadership band so a lone Main PM card never wastes a full row. */}
      <AgentGrid
        title="Leadership"
        titleHint="Board (Product Owner, Head of Marketing, Auditor) plus the Main PM"
        agents={[...getBoardAgents(agents), ...getMainPm(agents)]}
        agentStatuses={agentStatuses}
        agentUsage={agentUsageMap}
        isLoading={(isLoading || agentsLoading) && !isOffline}
      />

      <AgentGrid
        title="Backend Cell"
        titleHint="2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer"
        agents={getBackendAgents(agents)}
        agentStatuses={agentStatuses}
        agentUsage={agentUsageMap}
        isLoading={(isLoading || agentsLoading) && !isOffline}
      />

      <AgentGrid
        title="Frontend Cell"
        titleHint="2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer"
        agents={getFrontendAgents(agents)}
        agentStatuses={agentStatuses}
        agentUsage={agentUsageMap}
        isLoading={(isLoading || agentsLoading) && !isOffline}
      />

      <AgentGrid
        title="UX/UI Cell"
        titleHint="2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer"
        agents={getUxAgents(agents)}
        agentStatuses={agentStatuses}
        agentUsage={agentUsageMap}
        isLoading={(isLoading || agentsLoading) && !isOffline}
      />

      {/* Support section: the CEO-direct helpers — Intake/Prompter, Secretary,
          and the root PR Reviewer — only rendered when at least one matches */}
      {getSupportAgents(agents).length > 0 && (
        <AgentGrid
          title="Support"
          titleHint="CEO-direct helpers: Intake/Prompter, Secretary, and the root PR Reviewer"
          agents={getSupportAgents(agents)}
          agentStatuses={agentStatuses}
          agentUsage={agentUsageMap}
          isLoading={(isLoading || agentsLoading) && !isOffline}
        />
      )}
    </div>
  );
}
