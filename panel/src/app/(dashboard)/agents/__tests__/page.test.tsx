import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { AgentDefinition } from "@/lib/agent-definitions";

// CEO feedback round 2: Total Agents must reflect the full roster, not the
// orchestrator's live-instance count, and Board + Main PM must fold into one
// "Leadership" band instead of a lone Main PM card wasting a full row.

vi.mock("@/hooks/use-page-refresh", () => ({
  usePageRefresh: () => ({
    register: vi.fn(),
    unregister: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/hooks/use-usage", () => ({
  useAgentUsage: () => ({ data: [] }),
}));

const { useOrchestratorStatus, useWaitingAgents, useAgentDefinitions } =
  vi.hoisted(() => ({
    useOrchestratorStatus: vi.fn(),
    useWaitingAgents: vi.fn(),
    useAgentDefinitions: vi.fn(),
  }));

vi.mock("@/hooks/use-agents", () => ({
  useOrchestratorStatus,
  useWaitingAgents,
  useAgentDefinitions,
}));

vi.mock("@/components/agents", () => ({
  OrchestratorStatusCards: ({ rosterCount }: { rosterCount: number }) => (
    <div data-testid="orchestrator-status-cards" data-roster-count={rosterCount} />
  ),
  WaitingAgentsAlert: () => <div data-testid="waiting-agents-alert" />,
  AgentGrid: ({
    title,
    agents,
  }: {
    title: string;
    agents: AgentDefinition[];
  }) => (
    <div data-testid={"grid-" + title}>
      {agents.map((a) => a.id).join(",")}
    </div>
  ),
}));

import AgentsPage from "../page";

const AGENTS: AgentDefinition[] = [
  {
    id: "product-owner",
    name: "Product Owner",
    role: "product_owner" as AgentDefinition["role"],
    team: "board" as AgentDefinition["team"],
  },
  {
    id: "head-marketing",
    name: "Head of Marketing",
    role: "head_marketing" as AgentDefinition["role"],
    team: "board" as AgentDefinition["team"],
  },
  {
    id: "auditor",
    name: "Auditor",
    role: "auditor" as AgentDefinition["role"],
    team: "board" as AgentDefinition["team"],
  },
  {
    id: "main-pm",
    name: "Main PM",
    role: "main_pm" as AgentDefinition["role"],
    team: "main_pm" as AgentDefinition["team"],
  },
  {
    id: "be-dev-1",
    name: "Backend Dev 1",
    role: "developer" as AgentDefinition["role"],
    team: "backend" as AgentDefinition["team"],
  },
];

describe("AgentsPage", () => {
  beforeEach(() => {
    useAgentDefinitions.mockReturnValue({ data: AGENTS, isLoading: false });
    useOrchestratorStatus.mockReturnValue({
      data: {
        total_agents: 2, // deliberately far below the roster size
        by_state: { active: 2 },
        waiting_count: 0,
        agents: [],
      },
      isLoading: false,
      error: undefined,
      refetch: vi.fn(),
    });
    useWaitingAgents.mockReturnValue({ data: undefined });
  });

  it("passes the full roster size as the truthful Total Agents count, not the backend's live-instance total", () => {
    render(<AgentsPage />);
    const cards = screen.getByTestId("orchestrator-status-cards");
    expect(cards).toHaveAttribute("data-roster-count", "5");
  });

  it("folds Board and Main PM into one Leadership group instead of separate sections", () => {
    render(<AgentsPage />);
    expect(screen.getByTestId("grid-Leadership")).toHaveTextContent(
      "product-owner,head-marketing,auditor,main-pm",
    );
    expect(screen.queryByTestId("grid-Board")).not.toBeInTheDocument();
    expect(screen.queryByTestId("grid-Main PM")).not.toBeInTheDocument();
  });

  it("still renders the per-cell grids", () => {
    render(<AgentsPage />);
    expect(screen.getByTestId("grid-Backend Cell")).toHaveTextContent(
      "be-dev-1",
    );
  });

  it("does not render the Support grid when no support agents match", () => {
    render(<AgentsPage />);
    expect(screen.queryByTestId("grid-Support")).not.toBeInTheDocument();
  });
});
