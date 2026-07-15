import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OrchestratorStatusCards } from "../orchestrator-status";

// CEO feedback: "Total Agents shows 0 even though the full 25-agent roster
// renders below" — the card was reading status.total_agents, the backend's
// live in-memory instance count (roboco/runtime/orchestrator.py
// get_status_summary: len(self._instances)), not the roster. And "Active"
// keyed off by_state.running / by_state.ready, neither of which the backend
// ever emits (OrchestratorAgentState is offline/starting/active/
// waiting_short/waiting_long/idle/stopping) — so it silently always read 0.
// Both are now truthful: Total Agents = the roster prop, Active = by_state.active.

describe("OrchestratorStatusCards", () => {
  it("shows the roster size for Total Agents, independent of the backend's live-instance total_agents", () => {
    render(
      <OrchestratorStatusCards
        status={{
          total_agents: 2,
          by_state: { active: 2 },
          waiting_count: 0,
          agents: [],
        }}
        isLoading={false}
        rosterCount={25}
      />,
    );
    expect(screen.getByTestId("stat-total-agents")).toHaveTextContent("25");
  });

  it("counts Active from the real 'active' backend state, ignoring the nonexistent running/ready keys", () => {
    render(
      <OrchestratorStatusCards
        status={{
          total_agents: 5,
          by_state: { running: 99, ready: 99, active: 3, idle: 4 },
          waiting_count: 1,
          agents: [],
        }}
        isLoading={false}
        rosterCount={25}
      />,
    );
    expect(screen.getByTestId("stat-active")).toHaveTextContent("3");
    expect(screen.getByTestId("stat-waiting")).toHaveTextContent("1");
  });

  it("shows Stopped when there is no status snapshot (orchestrator unreachable)", () => {
    render(
      <OrchestratorStatusCards
        status={undefined}
        isLoading={false}
        rosterCount={25}
      />,
    );
    expect(screen.getByTestId("stat-orchestrator")).toHaveTextContent(
      "Stopped",
    );
  });

  it("shows Running once a status snapshot resolves, even with zero active agents", () => {
    render(
      <OrchestratorStatusCards
        status={{ total_agents: 0, by_state: {}, waiting_count: 0, agents: [] }}
        isLoading={false}
        rosterCount={25}
      />,
    );
    expect(screen.getByTestId("stat-orchestrator")).toHaveTextContent(
      "Running",
    );
    expect(screen.getByTestId("stat-active")).toHaveTextContent("0");
  });

  it("skeletons the roster cell while the roster query is loading, independent of the status query", () => {
    render(
      <OrchestratorStatusCards
        status={{
          total_agents: 0,
          by_state: { active: 1 },
          waiting_count: 0,
          agents: [],
        }}
        isLoading={false}
        rosterCount={0}
        rosterLoading
      />,
    );
    expect(screen.queryByTestId("stat-total-agents")).not.toBeInTheDocument();
    // Active isn't gated by rosterLoading — it already resolved.
    expect(screen.getByTestId("stat-active")).toHaveTextContent("1");
  });

  it("explains what each stat cell counts via a hover tooltip", async () => {
    const user = userEvent.setup();
    render(
      <OrchestratorStatusCards
        status={{
          total_agents: 5,
          by_state: { active: 3 },
          waiting_count: 1,
          agents: [],
        }}
        isLoading={false}
        rosterCount={25}
      />,
    );
    await user.hover(screen.getByTestId("stat-total-agents"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/roster/i);
  });
});
