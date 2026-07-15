import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Detail-page parity fix: the grid card's spawn affordance (SpawnAgentDialog,
// which collects task id + message) was already correct, but this page called
// spawnAgent.mutateAsync({ agentId }) directly from a bare button — no task,
// no message, no double-fire guard. Both bare buttons must now render the
// shared SpawnAgentDialog instead.

vi.mock("next/navigation", () => ({
  useParams: () => ({ agentId: "fe-dev-2" }),
  useRouter: () => ({ back: vi.fn() }),
}));

vi.mock("@/hooks/use-page-refresh", () => ({
  usePageRefresh: () => ({
    register: vi.fn(),
    unregister: vi.fn(),
    refresh: vi.fn(),
    loading: false,
    disabled: false,
  }),
}));

vi.mock("@/hooks/use-agents", () => ({
  useAgentStatus: vi.fn(),
  useAgentDefinition: vi.fn(() => ({ data: undefined })),
  useStopAgent: vi.fn(() => ({ mutateAsync: vi.fn() })),
}));

vi.mock("@/components/agents", () => ({
  AgentStatusCards: () => null,
  ResolveWaitDialog: () => null,
  AgentStreamViewer: () => null,
  AgentActivityPanel: () => null,
  SpawnAgentDialog: ({
    agentId,
    agentName,
    trigger,
  }: {
    agentId: string;
    agentName: string;
    trigger: React.ReactNode;
  }) => (
    <div
      data-testid="spawn-agent-dialog"
      data-agent-id={agentId}
      data-agent-name={agentName}
    >
      {trigger}
    </div>
  ),
}));

import { useAgentStatus } from "@/hooks/use-agents";
import AgentDetailPage from "../page";

describe("AgentDetailPage — spawn dialog parity", () => {
  it("renders SpawnAgentDialog (not a bare button) in the error state", () => {
    vi.mocked(useAgentStatus).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("not found"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAgentStatus>);

    render(<AgentDetailPage />);

    const dialog = screen.getByTestId("spawn-agent-dialog");
    expect(dialog).toHaveAttribute("data-agent-id", "fe-dev-2");
    expect(
      screen.getByRole("button", { name: /Spawn Agent/i }),
    ).toBeInTheDocument();
  });

  it("renders SpawnAgentDialog in the header when the agent is not active", () => {
    vi.mocked(useAgentStatus).mockReturnValue({
      data: {
        agent_id: "fe-dev-2",
        state: "stopped",
        task_id: null,
        error_count: 0,
        started_at: null,
        waiting_for: null,
      },
      isLoading: false,
      error: undefined,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAgentStatus>);

    render(<AgentDetailPage />);

    const dialog = screen.getByTestId("spawn-agent-dialog");
    expect(dialog).toHaveAttribute("data-agent-id", "fe-dev-2");
    expect(screen.getByRole("button", { name: "Spawn" })).toBeInTheDocument();
    // Active-state Stop buttons must not render alongside a down agent.
    expect(
      screen.queryByRole("button", { name: "Stop" }),
    ).not.toBeInTheDocument();
  });
});
