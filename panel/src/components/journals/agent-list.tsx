"use client";

import { Agent, Team, AgentRole } from "@/types";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentItem } from "./agent-item";

interface AgentListProps {
  agents: Agent[] | undefined;
  isLoading: boolean;
  selectedAgentId: string | null;
  onSelectAgent: (agentId: string) => void;
}

// Group agents by team
function groupByTeam(agents: Agent[]): Record<string, Agent[]> {
  const groups: Record<string, Agent[]> = {
    backend: [],
    frontend: [],
    ux_ui: [],
    marketing: [],
    board: [],
    management: [],
  };

  agents.forEach((agent) => {
    if (agent.team === Team.BACKEND) {
      groups.backend.push(agent);
    } else if (agent.team === Team.FRONTEND) {
      groups.frontend.push(agent);
    } else if (agent.team === Team.UX_UI) {
      groups.ux_ui.push(agent);
    } else if (agent.team === Team.MARKETING) {
      groups.marketing.push(agent);
    } else if (agent.team === Team.BOARD) {
      groups.board.push(agent);
    } else if (agent.team === Team.MAIN_PM || agent.role === AgentRole.MAIN_PM) {
      groups.management.push(agent);
    } else if (
      agent.role === AgentRole.PRODUCT_OWNER ||
      agent.role === AgentRole.HEAD_MARKETING ||
      agent.role === AgentRole.AUDITOR
    ) {
      groups.board.push(agent);
    }
  });

  return groups;
}

const TEAM_LABELS: Record<string, string> = {
  backend: "Backend",
  frontend: "Frontend",
  ux_ui: "UX/UI",
  marketing: "Marketing",
  board: "Board",
  management: "Management",
};

export function AgentList({
  agents,
  isLoading,
  selectedAgentId,
  onSelectAgent,
}: AgentListProps) {
  if (isLoading) {
    return (
      <div className="space-y-2 p-2">
        {[...Array(8)].map((_, i) => (
          <Skeleton key={i} className="h-12" />
        ))}
      </div>
    );
  }

  if (!agents || agents.length === 0) {
    return (
      <div className="p-4 text-center text-muted-foreground text-sm">
        No agents found
      </div>
    );
  }

  const grouped = groupByTeam(agents);

  return (
    <ScrollArea className="h-[calc(100vh-200px)]">
      <div className="p-2 space-y-4">
        {Object.entries(grouped).map(([teamKey, teamAgents]) => {
          if (teamAgents.length === 0) return null;
          return (
            <div key={teamKey}>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider px-2 mb-2">
                {TEAM_LABELS[teamKey]}
              </h3>
              <div className="space-y-1">
                {teamAgents.map((agent) => (
                  <AgentItem
                    key={agent.agent_id}
                    agent={agent}
                    isSelected={selectedAgentId === agent.agent_id}
                    onClick={() => onSelectAgent(agent.agent_id)}
                    hasEntries={true}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </ScrollArea>
  );
}
