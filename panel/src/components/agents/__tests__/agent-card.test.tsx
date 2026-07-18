import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AgentDefinition } from "@/lib/agent-definitions";
import type { AgentStatusResponse } from "@/types";

vi.mock("@/hooks/use-agents", () => ({
  useStopAgent: () => ({ mutateAsync: vi.fn() }),
}));

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
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

  it("sets a matching title on the actions menu trigger for the tooltip text", () => {
    // The DropdownMenu mock above (an inline div, not real Radix) swallows the
    // Tooltip's injected pointer handlers, so a hover-driven assertion isn't
    // reachable here — the real Radix composition mirrors task-actions.tsx's
    // proven working Tooltip-around-DropdownMenuTrigger pattern.
    render(<AgentCard agent={AGENT} agentStatus={statusOf()} />);
    expect(
      screen.getByRole("button", { name: "Agent actions" }),
    ).toHaveAttribute("title", "Agent actions");
  });

  it("explains the status dot/label via a hover tooltip reusing the state description map", async () => {
    const user = userEvent.setup();
    render(
      <AgentCard agent={AGENT} agentStatus={statusOf({ state: "active" })} />,
    );
    await user.hover(screen.getByText("active"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /actively working/i,
    );
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

  it("offers a DM quick-action that jumps to Conversations pre-targeted at this agent", async () => {
    const user = userEvent.setup();
    render(<AgentCard agent={AGENT} agentStatus={statusOf()} />);
    await user.click(screen.getByRole("button", { name: "DM this agent" }));
    expect(mockPush).toHaveBeenCalledWith(
      "/agents?tab=conversations&dm=be-dev-1",
    );
  });

  it("hides the DM quick-action for a role that can't read/answer a DM", () => {
    const auditor = {
      id: "auditor",
      name: "Auditor",
      role: "auditor",
      team: "board",
    } as unknown as AgentDefinition;
    render(<AgentCard agent={auditor} agentStatus={statusOf()} />);
    expect(
      screen.queryByRole("button", { name: "DM this agent" }),
    ).not.toBeInTheDocument();
  });

  it("hides the DM quick-action for the CEO card", () => {
    const ceo = {
      id: "ceo",
      name: "CEO",
      role: "ceo",
      team: "board",
    } as unknown as AgentDefinition;
    render(<AgentCard agent={ceo} agentStatus={statusOf()} />);
    expect(
      screen.queryByRole("button", { name: "DM this agent" }),
    ).not.toBeInTheDocument();
  });
});
