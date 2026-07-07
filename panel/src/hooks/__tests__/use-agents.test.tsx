import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";

// H18: useAgents caches the roster with staleTime 5min and no refetchInterval.
// A live status change (idle→running) polled by useOrchestratorStatus every
// 10s must propagate to the roster immediately — the statusEpoch in the
// queryKey makes the roster refetch when the live snapshot changes. Pre-fix
// the roster stays "idle" for up to 5min.

const { getStatus, getAll } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  getAll: vi.fn(),
}));

vi.mock("@/lib/api/orchestrator", () => ({
  orchestratorApi: { getStatus },
}));

vi.mock("@/lib/api/agents", () => ({
  agentsApi: { getAll },
}));

import { useAgents, agentKeys } from "@/hooks/use-agents";

const DEF = [
  { id: "a", uuid: "u-a", name: "Agent A", role: "developer", team: "backend" },
];

describe("useAgents — H18 statusEpoch", () => {
  let client: QueryClient;

  function wrapper({ children }: { children: ReactNode }) {
    client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 5 * 60 * 1000 },
        mutations: { retry: false },
      },
    });
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }

  beforeEach(() => {
    getStatus.mockReset();
    getAll.mockReset();
    getAll.mockResolvedValue(DEF);
  });

  it("re-derives roster when the live status poll changes", async () => {
    getStatus.mockResolvedValue({
      total_agents: 1,
      by_state: { idle: 1 },
      waiting_count: 0,
      agents: [{ agent_id: "a", state: "idle" }],
    });

    const { result } = renderHook(() => useAgents(), { wrapper });

    await waitFor(() => expect(result.current.data?.[0]?.status).toBe("idle"));

    // Live status poll now reports the agent as running.
    getStatus.mockResolvedValue({
      total_agents: 1,
      by_state: { running: 1 },
      waiting_count: 0,
      agents: [{ agent_id: "a", state: "running" }],
    });

    // Trigger the status query refetch (what the 10s refetchInterval does in
    // production). The new live snapshot must invalidate the roster queryKey
    // and re-derive the roster immediately.
    await act(async () => {
      await client.refetchQueries({ queryKey: agentKeys.status() });
    });

    await waitFor(() =>
      expect(result.current.data?.[0]?.status).toBe("running"),
    );
    expect(getStatus).toHaveBeenCalledTimes(2);
  });
});
