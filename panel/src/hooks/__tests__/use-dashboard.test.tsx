import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";

// M40: useMetrics ran a Promise.all that called dashboardApi.getAgentStatus()
// every 60s while useAgentStatus polled the SAME endpoint every 10s — two
// queries, same key path, redundant fetches. Pre-fix useMetrics issues its
// own getAgentStatus() call so the spy fires twice when both hooks mount.
// Post-fix useMetrics reads queryClient.getQueryData(dashboardKeys.agentStatus())
// and only falls back to fetchQuery (which dedupes against the in-flight
// useAgentStatus query) on a cold cache — net one call.

const { getAgentStatus, getVelocityMetrics, getBlockerMetrics } = vi.hoisted(
  () => ({
    getAgentStatus: vi.fn(),
    getVelocityMetrics: vi.fn(),
    getBlockerMetrics: vi.fn(),
  }),
);

vi.mock("@/lib/api/dashboard", () => ({
  dashboardApi: { getAgentStatus, getVelocityMetrics, getBlockerMetrics },
}));

import { useAgentStatus, useMetrics } from "@/hooks/use-dashboard";

const AGENT_STATUS = {
  total_agents: 3,
  by_state: { running: 1, idle: 2 },
  waiting_count: 0,
};

const VELOCITY = {
  tasks_completed_today: 5,
  tasks_completed_week: 20,
  average_completion_time_hours: 2,
};

const BLOCKERS = {
  total_blocked: 1,
  blocked_by_team: { backend: 1 },
  longest_blocked_hours: 4,
};

describe("M40 — useMetrics dedupes agent-status poll", () => {
  let client: QueryClient;

  function wrapper({ children }: { children: ReactNode }) {
    client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 0, refetchInterval: false },
        mutations: { retry: false },
      },
    });
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }

  beforeEach(() => {
    getAgentStatus.mockReset();
    getVelocityMetrics.mockReset();
    getBlockerMetrics.mockReset();
    getAgentStatus.mockResolvedValue(AGENT_STATUS);
    getVelocityMetrics.mockResolvedValue(VELOCITY);
    getBlockerMetrics.mockResolvedValue(BLOCKERS);
  });

  it("useMetrics reads agent counts from the useAgentStatus cache (one fetch)", async () => {
    renderHook(() => ({ status: useAgentStatus(), metrics: useMetrics() }), {
      wrapper,
    });

    await waitFor(() => expect(getVelocityMetrics).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getAgentStatus).toHaveBeenCalledTimes(1));

    expect(getBlockerMetrics).toHaveBeenCalledTimes(1);
    expect(getAgentStatus).toHaveBeenCalledTimes(1);
  });
});
