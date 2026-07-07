import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";

// M41: scorecard data is slow-moving aggregated cycle-time/rework; polling
// every 60s was excessive load for data that barely moves. Both scorecard
// hooks should refetch every 5min (300_000ms) via a shared constant.

const { getMemberScorecard, getOrgScorecard } = vi.hoisted(() => ({
  getMemberScorecard: vi.fn(),
  getOrgScorecard: vi.fn(),
}));

vi.mock("@/lib/api/observability", () => ({
  observabilityApi: { getMemberScorecard, getOrgScorecard },
}));

import { useMemberScorecard, useOrgScorecard } from "@/hooks/use-observability";

const UUID = "00000000-0000-0000-0002-000000000001";

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, refetchInterval: false },
    },
  });
}

function wrapperFor(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

describe("M41 — scorecard refetchInterval is 5min", () => {
  beforeEach(() => {
    getMemberScorecard.mockReset();
    getOrgScorecard.mockReset();
    getMemberScorecard.mockResolvedValue({});
    getOrgScorecard.mockResolvedValue({});
  });

  it("useMemberScorecard refetches every 300_000ms", async () => {
    const client = makeClient();
    renderHook(() => useMemberScorecard(UUID), {
      wrapper: wrapperFor(client),
    });

    await waitFor(() => expect(getMemberScorecard).toHaveBeenCalledOnce());

    const query = client
      .getQueryCache()
      .getAll()
      .find((q) => q.queryKey[2] === "member");
    expect(
      (query?.options as { refetchInterval?: number }).refetchInterval,
    ).toBe(300_000);
  });

  it("useOrgScorecard refetches every 300_000ms", async () => {
    const client = makeClient();
    renderHook(() => useOrgScorecard(30), {
      wrapper: wrapperFor(client),
    });

    await waitFor(() => expect(getOrgScorecard).toHaveBeenCalledOnce());

    const query = client
      .getQueryCache()
      .getAll()
      .find((q) => q.queryKey[2] === "org");
    expect(
      (query?.options as { refetchInterval?: number }).refetchInterval,
    ).toBe(300_000);
  });
});
