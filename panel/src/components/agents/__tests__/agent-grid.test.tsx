import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { AgentDefinition } from "@/lib/agent-definitions";

// Isolate AgentGrid from the real AgentCard (which pulls in mutation hooks,
// dropdown, and SpawnAgentDialog) — this file only covers grid-level
// concerns: the section header's count badge, one card per agent, and the
// loading-skeleton branch.
vi.mock("../agent-card", () => ({
  AgentCard: ({ agent }: { agent: AgentDefinition }) => (
    <div data-testid="agent-card">{agent.name}</div>
  ),
}));

import { AgentGrid } from "../agent-grid";

const AGENTS: AgentDefinition[] = [
  { id: "be-dev-1", name: "Backend Dev 1", role: null, team: null },
  { id: "be-dev-2", name: "Backend Dev 2", role: null, team: null },
];

describe("AgentGrid", () => {
  it("shows a count badge inline with the section title", () => {
    render(
      <AgentGrid
        title="Backend Cell"
        agents={AGENTS}
        agentStatuses={{}}
        isLoading={false}
      />,
    );
    expect(screen.getByText("Backend Cell")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("renders one AgentCard per agent", () => {
    render(
      <AgentGrid
        title="Backend Cell"
        agents={AGENTS}
        agentStatuses={{}}
        isLoading={false}
      />,
    );
    const cards = screen.getAllByTestId("agent-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("Backend Dev 1");
    expect(cards[1]).toHaveTextContent("Backend Dev 2");
  });

  it("shows skeleton placeholders while loading, not the real cards", () => {
    render(
      <AgentGrid
        title="Backend Cell"
        agents={AGENTS}
        agentStatuses={{}}
        isLoading
      />,
    );
    expect(screen.queryByTestId("agent-card")).not.toBeInTheDocument();
  });

  it("reflects a zero-agent group truthfully in the count badge", () => {
    render(
      <AgentGrid title="Support" agents={[]} agentStatuses={{}} isLoading={false} />,
    );
    expect(screen.getByText("Support")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
