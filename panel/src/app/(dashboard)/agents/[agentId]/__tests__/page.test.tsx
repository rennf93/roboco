import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Detail-page parity fix: the grid card's spawn affordance (SpawnAgentDialog,
// which collects task id + message) was already correct, but this page called
// spawnAgent.mutateAsync({ agentId }) directly from a bare button — no task,
// no message, no double-fire guard. Both bare buttons must now render the
// shared SpawnAgentDialog instead.
//
// Whole-page-replaced-by-error fix: a stopped agent's live-status query 404s
// (orchestrator has no running instance) while the agent's roster identity
// still resolves fine. That must degrade the live-status area only — not
// discard the DB-backed header/activity content already rendered above it.
// The full-page fatal card is reserved for a genuinely invalid agent id
// (the roster lookup itself failing).

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
  useAgentDefinition: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    error: undefined,
  })),
  useStopAgent: vi.fn(() => ({ mutateAsync: vi.fn() })),
}));

vi.mock("@/components/agents", () => ({
  AgentStatusCards: () => <div data-testid="agent-status-cards" />,
  ResolveWaitDialog: () => null,
  AgentStreamViewer: () => null,
  AgentActivityPanel: () => <div data-testid="agent-activity-panel" />,
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

import { useAgentStatus, useAgentDefinition } from "@/hooks/use-agents";
import AgentDetailPage from "../page";

describe("AgentDetailPage — spawn dialog parity", () => {
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

describe("AgentDetailPage — live-status error degrades, doesn't discard content", () => {
  it("keeps header + activity panel and shows a not-running banner when the roster resolves but live status 404s", () => {
    vi.mocked(useAgentDefinition).mockReturnValue({
      data: { id: "fe-dev-2", uuid: "uuid-1", name: "Frontend Dev 2" },
      isLoading: false,
      error: undefined,
    } as unknown as ReturnType<typeof useAgentDefinition>);
    vi.mocked(useAgentStatus).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Agent fe-dev-2 not found"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAgentStatus>);

    render(<AgentDetailPage />);

    // DB-backed content that loaded independently of live status must stay.
    expect(screen.getByText("Frontend Dev 2")).toBeInTheDocument();
    expect(screen.getByTestId("agent-activity-panel")).toBeInTheDocument();

    // Live-status area degrades to an inline banner, not a whole-page card.
    expect(screen.getByText("Not running")).toBeInTheDocument();
    expect(
      screen.queryByText("Failed to load agent status"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-status-cards")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Spawn Agent/i }),
    ).toBeInTheDocument();
  });

  it("shows the whole-page fatal card only when the roster lookup itself fails (invalid id)", () => {
    vi.mocked(useAgentDefinition).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Agent not found"),
    } as unknown as ReturnType<typeof useAgentDefinition>);
    vi.mocked(useAgentStatus).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Agent not found"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAgentStatus>);

    render(<AgentDetailPage />);

    expect(screen.getByText("Failed to load agent status")).toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-activity-panel"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Spawn Agent/i }),
    ).toBeInTheDocument();
  });

  it("shows the status skeleton (not the banner) while the roster is still loading, even if status already errored", () => {
    vi.mocked(useAgentDefinition).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: undefined,
    } as unknown as ReturnType<typeof useAgentDefinition>);
    vi.mocked(useAgentStatus).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Agent not found"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAgentStatus>);

    render(<AgentDetailPage />);

    // Definition still resolving — not fatal yet, page renders normally.
    expect(
      screen.queryByText("Failed to load agent status"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Not running")).toBeInTheDocument();
  });
});
