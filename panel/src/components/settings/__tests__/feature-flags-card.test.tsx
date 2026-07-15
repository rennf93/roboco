import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Deferred mutationFn: the test holds every pending resolver in a queue so it
// can freeze multiple toggles mid-flight and release them one at a time. This
// exercises the REAL useMutation isPending/variables state rather than a
// stubbed hook. `vi.hoisted` keeps the mock fns initialized before the hoisted
// vi.mock factory runs.
const { resolveQueue } = vi.hoisted(() => ({
  resolveQueue: { current: [] as Array<(v: unknown) => void> },
}));

const { setFeatureFlag, getFeatureFlags } = vi.hoisted(() => ({
  setFeatureFlag: vi.fn(
    () =>
      new Promise((r) => {
        resolveQueue.current.push(r as (v: unknown) => void);
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

// M42: off-transitions (true→false) must open a confirm AlertDialog and only
// fire the mutation on confirm; on-transitions (false→true) fire immediately.
// pendingKeys tracks every in-flight toggle so each row locks independently.
describe("FeatureFlagsCard — M42 off-transition confirm + pending-keys Set", () => {
  beforeEach(() => {
    setFeatureFlag.mockClear();
    getFeatureFlags.mockClear();
    resolveQueue.current = [];
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("off-transition opens a confirm dialog and defers the mutation until confirmed", async () => {
    render(withQueryClient(<FeatureFlagsCard />));

    const alpha = await screen.findByRole("switch", { name: "Alpha" });
    expect(alpha).toBeChecked();

    // Click the ON switch → off-transition → confirm dialog opens.
    fireEvent.click(alpha);

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toBeInTheDocument();

    // Mutation is NOT fired until the operator confirms.
    expect(setFeatureFlag).not.toHaveBeenCalled();

    // Confirm → mutation fires with enabled=false.
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(setFeatureFlag).toHaveBeenCalledWith("alpha", false),
    );
  });

  it("on-transition fires immediately without a confirm dialog", async () => {
    render(withQueryClient(<FeatureFlagsCard />));

    const beta = await screen.findByRole("switch", { name: "Beta" });
    expect(beta).not.toBeChecked();

    // Click the OFF switch → on-transition → fires immediately, no dialog.
    fireEvent.click(beta);
    await waitFor(() =>
      expect(setFeatureFlag).toHaveBeenCalledWith("beta", true),
    );

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("pendingKeys tracks every in-flight toggle so each row stays locked", async () => {
    render(withQueryClient(<FeatureFlagsCard />));

    const alpha = await screen.findByRole("switch", { name: "Alpha" });
    const beta = await screen.findByRole("switch", { name: "Beta" });

    // Alpha: off-transition → confirm → mutation in flight (deferred).
    fireEvent.click(alpha);
    fireEvent.click(await screen.findByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(setFeatureFlag).toHaveBeenCalledWith("alpha", false),
    );
    await waitFor(() => expect(alpha).toBeDisabled());

    // Beta: on-transition → fires immediately, mutation in flight (deferred).
    fireEvent.click(beta);
    await waitFor(() =>
      expect(setFeatureFlag).toHaveBeenCalledWith("beta", true),
    );

    // Both switches are locked while their toggles are in flight. Pre-fix only
    // the latest toggle's key was tracked, so Alpha would unlock when Beta
    // started.
    await waitFor(() => expect(beta).toBeDisabled());
    expect(alpha).toBeDisabled();

    // Resolve Alpha (FIFO) → Alpha unlocks; Beta stays locked until its own
    // resolves.
    resolveQueue.current.shift()?.(undefined);
    await waitFor(() => expect(alpha).not.toBeDisabled());
    expect(beta).toBeDisabled();

    // Resolve Beta → Beta unlocks.
    resolveQueue.current.shift()?.(undefined);
    await waitFor(() => expect(beta).not.toBeDisabled());
  });

  // W9-5 follow-up: every real flag key gets a one-line hover tip on its
  // label (FLAG_TOOLTIPS in feature-flags-card.tsx); an unmapped key (the
  // "alpha"/"beta" fixtures above) renders bare per HelpTip's falsy short-
  // circuit. TooltipTrigger always stamps data-state ("closed" while
  // unopened) onto its asChild target, so its presence/absence is a reliable
  // proxy for "is this label tooltip-wrapped" without simulating hover.
  it("attaches the mapped tooltip to a real flag key and leaves unmapped keys bare", async () => {
    getFeatureFlags.mockResolvedValueOnce({
      flags: [
        { key: "alpha", label: "Alpha", enabled: true },
        {
          key: "external_pr_enabled",
          label: "External PR Review",
          enabled: true,
        },
      ],
      note: "Changes take effect on the next backend restart.",
    });
    render(withQueryClient(<FeatureFlagsCard />));

    const mapped = await screen.findByText("External PR Review");
    expect(mapped.getAttribute("data-state")).toBe("closed");

    const unmapped = screen.getByText("Alpha");
    expect(unmapped.getAttribute("data-state")).toBeNull();
  });
});
