import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { AgentDefinition } from "@/lib/agent-definitions";
import type { AgentStatusResponse } from "@/types";

vi.mock("@/hooks/use-agents", () => ({
  useStopAgent: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Render the dropdown inline so its items are queryable without Radix's
// portal/pointer machinery — mirrors task-header's test convention
// (task-header-ceo-approve.test.tsx).
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuItem: ({
    children,
    onClick,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
  }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
  DropdownMenuSeparator: () => null,
}));

vi.mock("../spawn-agent-dialog", () => ({
  SpawnAgentDialog: ({ agentName }: { agentName: string }) => (
    <div data-testid="spawn-dialog">{agentName}</div>
  ),
}));

import { AgentCard } from "../agent-card";

const AGENT = {
  id: "be-dev-1",
  name: "Backend Dev 1",
  role: "developer",
  team: "backend",
} as unknown as AgentDefinition;

function statusOf(
  overrides: Partial<AgentStatusResponse> = {},
): AgentStatusResponse {
  return {
    agent_id: "be-dev-1",
    state: "active",
    task_id: null,
    error_count: 0,
    started_at: null,
    waiting_for: null,
    ...overrides,
  };
}

describe("AgentCard", () => {
  it("renders state as a colored dot with an inline label, not the old badge chrome", () => {
    render(
      <AgentCard agent={AGENT} agentStatus={statusOf({ state: "active" })} />,
    );
    expect(document.querySelector(".bg-green-500.rounded-full")).toBeTruthy();
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("renders role and team as a single muted line", () => {
    render(<AgentCard agent={AGENT} agentStatus={null} />);
    expect(screen.getByText("developer • backend")).toBeInTheDocument();
  });

  it("prioritizes an error detail line over waiting and task", () => {
    render(
      <AgentCard
        agent={AGENT}
        agentStatus={statusOf({
          error_count: 2,
          waiting_for: "a reply",
          task_id: "abcdef1234567890",
        })}
      />,
    );
    expect(screen.getByText("2 errors")).toBeInTheDocument();
    expect(screen.queryByText(/Waiting/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Task /)).not.toBeInTheDocument();
  });

  it("falls back to a task detail line when there is no error or wait", () => {
    render(
      <AgentCard
        agent={AGENT}
        agentStatus={statusOf({ task_id: "abcdef1234567890" })}
      />,
    );
    expect(screen.getByText(/Task abcdef12/)).toBeInTheDocument();
  });

  it("offers Spawn for a down agent, and View Details + Stop for an active one", () => {
    const { rerender } = render(
      <AgentCard agent={AGENT} agentStatus={statusOf({ state: "stopped" })} />,
    );
    expect(screen.getByTestId("spawn-dialog")).toBeInTheDocument();
    expect(screen.queryByText("View Details")).not.toBeInTheDocument();

    rerender(
      <AgentCard agent={AGENT} agentStatus={statusOf({ state: "active" })} />,
    );
    expect(screen.queryByTestId("spawn-dialog")).not.toBeInTheDocument();
    expect(screen.getByText("View Details")).toBeInTheDocument();
    expect(screen.getByText("Stop Gracefully")).toBeInTheDocument();
    expect(screen.getByText("Force Stop")).toBeInTheDocument();
  });

  it("keeps the actions menu trigger accessible by name", () => {
    render(<AgentCard agent={AGENT} agentStatus={statusOf()} />);
    expect(
      screen.getByRole("button", { name: "Agent actions" }),
    ).toBeInTheDocument();
  });

  it("shows a compact one-line token/cost readout when usage data is present", () => {
    render(
      <AgentCard
        agent={AGENT}
        agentStatus={statusOf()}
        usageRow={{
          agent_slug: "be-dev-1",
          tokens_input: 8000,
          tokens_output: 4300,
          total_tokens: 12300,
          cost_usd: 0.0421,
          pct_of_total: 0.1,
        }}
      />,
    );
    expect(screen.getByText(/12\.3K tok/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.0421/)).toBeInTheDocument();
  });
});
