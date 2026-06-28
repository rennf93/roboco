import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Deferred mutationFn: the test holds `resolveSet` so it can freeze the toggle
// mutation mid-flight and observe the per-control disabled state, then
// release it. This exercises the REAL useMutation isPending/variables state
// rather than a stubbed hook. `vi.hoisted` keeps the mock fns initialized
// before the hoisted vi.mock factory runs.
// Hold the deferred mutation resolver so the test can freeze the toggle
// mid-flight and observe the per-control disabled state, then release it.
const { resolveSetRef } = vi.hoisted(() => ({
  resolveSetRef: { current: null as null | ((v: unknown) => void) },
}));

const { setFeatureFlag, getFeatureFlags } = vi.hoisted(() => ({
  setFeatureFlag: vi.fn(
    () =>
      new Promise((r) => {
        resolveSetRef.current = r as (v: unknown) => void;
      }),
  ),
  getFeatureFlags: vi.fn(async () => ({
    flags: [
      { key: "alpha", label: "Alpha", enabled: true },
      { key: "beta", label: "Beta", enabled: false },
    ],
    note: "Changes take effect on the next backend restart.",
  })),
}));

vi.mock("@/lib/api", () => ({
  settingsApi: { getFeatureFlags, setFeatureFlag },
}));

import { FeatureFlagsCard } from "../feature-flags-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("FeatureFlagsCard — per-control disable during a toggle (F084)", () => {
  beforeEach(() => {
    setFeatureFlag.mockClear();
    getFeatureFlags.mockClear();
    resolveSetRef.current = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("disables only the flag being toggled, not every flag's switch", async () => {
    render(withQueryClient(<FeatureFlagsCard />));

    const alpha = await screen.findByRole("switch", { name: "Alpha" });
    const beta = await screen.findByRole("switch", { name: "Beta" });
    expect(alpha).not.toBeDisabled();
    expect(beta).not.toBeDisabled();

    // Toggle Alpha off — the mutation stays pending (deferred mutationFn).
    fireEvent.click(alpha);
    await waitFor(() =>
      expect(setFeatureFlag).toHaveBeenCalledWith("alpha", false),
    );

    // Alpha's switch locks while its toggle is in flight; Beta stays usable so
    // the operator can flip an independent flag at the same time. Before the
    // fix every switch shared `disabled={toggleMutation.isPending}`.
    await waitFor(() => expect(alpha).toBeDisabled());
    expect(beta).not.toBeDisabled();

    // Mutation resolves → Alpha unlocks again.
    resolveSetRef.current?.(undefined);
    await waitFor(() => expect(alpha).not.toBeDisabled());
    expect(beta).not.toBeDisabled();
  });
});
