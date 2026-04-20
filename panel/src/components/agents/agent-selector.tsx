"use client";

import { useMemo } from "react";
import { useAgentDefinitions } from "@/hooks/use-agents";
import { Team, AgentRole } from "@/types";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { User, Users } from "lucide-react";
import { resolveToSlug } from "@/lib/agent-utils";

interface AgentSelectorProps {
  value: string | null;
  onChange: (value: string | null) => void;
  placeholder?: string;
  filterByTeam?: Team;
  filterByRoles?: AgentRole[];
  disabled?: boolean;
  allowClear?: boolean;
}

// Role display names
const ROLE_LABELS: Record<AgentRole, string> = {
  [AgentRole.SYSTEM]: "System",
  [AgentRole.CEO]: "CEO",
  [AgentRole.PRODUCT_OWNER]: "Product Owner",
  [AgentRole.HEAD_MARKETING]: "Head Marketing",
  [AgentRole.AUDITOR]: "Auditor",
  [AgentRole.MAIN_PM]: "Main PM",
  [AgentRole.CELL_PM]: "Cell PM",
  [AgentRole.DEVELOPER]: "Developer",
  [AgentRole.QA]: "QA",
  [AgentRole.DOCUMENTER]: "Documenter",
};

export function AgentSelector({
  value,
  onChange,
  placeholder = "Select agent...",
  filterByTeam,
  filterByRoles,
  disabled = false,
  allowClear = true,
}: AgentSelectorProps) {
  const { data: agents = [], isLoading } = useAgentDefinitions();

  // Group agents by team
  const groupedAgents = useMemo(() => {
    let filtered = agents;

    // Apply team filter - also match by role for Board and Main PM
    if (filterByTeam) {
      filtered = filtered.filter((a) => {
        // Direct team match
        if (a.team === filterByTeam) return true;

        // For Board team, also include board-level roles
        if (filterByTeam === Team.BOARD && (
          a.role === AgentRole.PRODUCT_OWNER ||
          a.role === AgentRole.HEAD_MARKETING ||
          a.role === AgentRole.AUDITOR
        )) return true;

        // For Main PM team, also include Main PM role
        if (filterByTeam === Team.MAIN_PM && a.role === AgentRole.MAIN_PM) return true;

        return false;
      });
    }

    // Apply role filter
    if (filterByRoles && filterByRoles.length > 0) {
      filtered = filtered.filter((a) => a.role && filterByRoles.includes(a.role));
    }

    // Group by team following org hierarchy
    const groups: Record<string, typeof filtered> = {
      board: [],
      main_pm: [],
      backend: [],
      frontend: [],
      ux_ui: [],
      marketing: [],
    };

    for (const agent of filtered) {
      if (agent.team === Team.BOARD ||
          agent.role === AgentRole.PRODUCT_OWNER ||
          agent.role === AgentRole.HEAD_MARKETING ||
          agent.role === AgentRole.AUDITOR) {
        groups.board.push(agent);
      } else if (agent.team === Team.MAIN_PM || agent.role === AgentRole.MAIN_PM) {
        groups.main_pm.push(agent);
      } else if (agent.team === Team.BACKEND) {
        groups.backend.push(agent);
      } else if (agent.team === Team.FRONTEND) {
        groups.frontend.push(agent);
      } else if (agent.team === Team.UX_UI) {
        groups.ux_ui.push(agent);
      } else if (agent.team === Team.MARKETING) {
        groups.marketing.push(agent);
      }
      // Note: Agents without team are not grouped - they remain ungrouped
      // The orchestrator handles automatic routing for unassigned tasks
    }

    return groups;
  }, [agents, filterByTeam, filterByRoles]);

  // Find selected agent for display (resolve UUID to slug if needed)
  const selectedAgent = useMemo(() => {
    if (!value) return null;
    const resolvedValue = resolveToSlug(value);
    return agents.find((a) => a.id === resolvedValue || a.id === value);
  }, [agents, value]);

  const handleValueChange = (newValue: string) => {
    if (newValue === "__clear__") {
      onChange(null);
    } else {
      onChange(newValue);
    }
  };

  // Resolve value to slug for proper Select matching
  const selectValue = value ? resolveToSlug(value) || value : "";

  return (
    <Select
      value={selectValue}
      onValueChange={handleValueChange}
      disabled={disabled || isLoading}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder}>
          {selectedAgent ? (
            <div className="flex items-center gap-2">
              <User className="h-4 w-4" />
              <span>{selectedAgent.name}</span>
              {selectedAgent.role && (
                <Badge variant="outline" className="text-xs">
                  {ROLE_LABELS[selectedAgent.role] || selectedAgent.role}
                </Badge>
              )}
            </div>
          ) : (
            placeholder
          )}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {allowClear && value && (
          <SelectItem value="__clear__" className="text-muted-foreground">
            <span className="flex items-center gap-2">
              <Users className="h-4 w-4" />
              Unassigned
            </span>
          </SelectItem>
        )}

        {/* Board */}
        {groupedAgents.board.length > 0 && (
          <SelectGroup>
            <SelectLabel>Board</SelectLabel>
            {groupedAgents.board.map((agent) => (
              <SelectItem key={agent.id} value={agent.id}>
                <div className="flex items-center gap-2">
                  <span>{agent.name}</span>
                  {agent.role && (
                    <Badge variant="secondary" className="text-xs">
                      {ROLE_LABELS[agent.role] || agent.role}
                    </Badge>
                  )}
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Main PM */}
        {groupedAgents.main_pm.length > 0 && (
          <SelectGroup>
            <SelectLabel>Main PM</SelectLabel>
            {groupedAgents.main_pm.map((agent) => (
              <SelectItem key={agent.id} value={agent.id}>
                <div className="flex items-center gap-2">
                  <span>{agent.name}</span>
                  {agent.role && (
                    <Badge variant="secondary" className="text-xs">
                      {ROLE_LABELS[agent.role] || agent.role}
                    </Badge>
                  )}
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Backend */}
        {groupedAgents.backend.length > 0 && (
          <SelectGroup>
            <SelectLabel>Backend Team</SelectLabel>
            {groupedAgents.backend.map((agent) => (
              <SelectItem key={agent.id} value={agent.id}>
                <div className="flex items-center gap-2">
                  <span>{agent.name}</span>
                  {agent.role && (
                    <Badge variant="secondary" className="text-xs">
                      {ROLE_LABELS[agent.role] || agent.role}
                    </Badge>
                  )}
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Frontend */}
        {groupedAgents.frontend.length > 0 && (
          <SelectGroup>
            <SelectLabel>Frontend Team</SelectLabel>
            {groupedAgents.frontend.map((agent) => (
              <SelectItem key={agent.id} value={agent.id}>
                <div className="flex items-center gap-2">
                  <span>{agent.name}</span>
                  {agent.role && (
                    <Badge variant="secondary" className="text-xs">
                      {ROLE_LABELS[agent.role] || agent.role}
                    </Badge>
                  )}
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* UX/UI */}
        {groupedAgents.ux_ui.length > 0 && (
          <SelectGroup>
            <SelectLabel>UX/UI Team</SelectLabel>
            {groupedAgents.ux_ui.map((agent) => (
              <SelectItem key={agent.id} value={agent.id}>
                <div className="flex items-center gap-2">
                  <span>{agent.name}</span>
                  {agent.role && (
                    <Badge variant="secondary" className="text-xs">
                      {ROLE_LABELS[agent.role] || agent.role}
                    </Badge>
                  )}
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Marketing */}
        {groupedAgents.marketing.length > 0 && (
          <SelectGroup>
            <SelectLabel>Marketing Team</SelectLabel>
            {groupedAgents.marketing.map((agent) => (
              <SelectItem key={agent.id} value={agent.id}>
                <div className="flex items-center gap-2">
                  <span>{agent.name}</span>
                  {agent.role && (
                    <Badge variant="secondary" className="text-xs">
                      {ROLE_LABELS[agent.role] || agent.role}
                    </Badge>
                  )}
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}
      </SelectContent>
    </Select>
  );
}
