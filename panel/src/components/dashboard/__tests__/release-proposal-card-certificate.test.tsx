import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReleaseCertificate, ReleaseProposal } from "@/lib/api/release";

// Covers the "Download certificate" button (bfb48210): the happy-path
// download trigger and the 404-to-null toast path. Unlike the sibling
// release-proposal-card.test.tsx, react-query itself is NOT mocked here —
// only releaseApi — so the real useMutation onSuccess/onError callbacks run.
const { getProposal, getCertificate } = vi.hoisted(() => ({
  getProposal: vi.fn(),
  getCertificate: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  releaseApi: { getProposal, approve: vi.fn(), reject: vi.fn(), getCertificate },
}));

const { toastInfo, toastError } = vi.hoisted(() => ({
  toastInfo: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: toastError, info: toastInfo },
}));

import { ReleaseProposalCard } from "../release-proposal-card";
import { PageRefreshProvider } from "@/components/providers";

function withProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <PageRefreshProvider>{ui}</PageRefreshProvider>
    </QueryClientProvider>
  );
}

function buildProposal(): ReleaseProposal {
  return {
    task_id: "t1",
    title: "Cut v0.14.0",
    status: "awaiting_ceo_approval",
    required_changes: null,
    report: {
      proposed_version: "0.14.0",
      bump_kind: "minor",
      change_summary: ["feat: metrics"],
      drafted_changelog: "## 0.14.0\n- metrics",
      version_bump_plan: ["pyproject.toml"],
      gaps: [],
      migration_notes: [],
      gate_state: "green",
    },
  };
}

function buildCertificate(): ReleaseCertificate {
  return {
    version: "0.14.0",
    generated_at: "2026-09-01T00:00:00Z",
    ci_verdict: "green",
    conventions_clean: true,
    ceo_approved_at: "2026-09-01T00:05:00Z",
    changelog_excerpt: "## 0.14.0",
    task_states: [],
    findings_summary: {
      open: { blocker: 0, major: 0, minor: 0, nit: 0 },
      closed: { blocker: 0, major: 0, minor: 0, nit: 0 },
      waived: { blocker: 0, major: 0, minor: 0, nit: 0 },
    },
  };
}

describe("ReleaseProposalCard — Download certificate button", () => {
  beforeEach(() => {
    getProposal.mockResolvedValue(buildProposal());
    getCertificate.mockReset();
    toastInfo.mockClear();
    toastError.mockClear();
    globalThis.URL.createObjectURL = vi.fn(() => "blob:mock-url");
    globalThis.URL.revokeObjectURL = vi.fn();
  });

  it("triggers a JSON blob download on a successful (published) certificate fetch", async () => {
    getCertificate.mockResolvedValue(buildCertificate());
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    render(withProviders(<ReleaseProposalCard />));
    const button = await screen.findByRole("button", {
      name: /Download certificate/i,
    });
    await userEvent.click(button);

    await waitFor(() => expect(getCertificate).toHaveBeenCalledWith("0.14.0"));
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(globalThis.URL.createObjectURL).toHaveBeenCalled();
    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    expect(toastInfo).not.toHaveBeenCalled();

    clickSpy.mockRestore();
  });

  it("shows an info toast instead of downloading when the version hasn't published yet (the no-CI-repo/404 case)", async () => {
    getCertificate.mockResolvedValue(null);
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    render(withProviders(<ReleaseProposalCard />));
    const button = await screen.findByRole("button", {
      name: /Download certificate/i,
    });
    await userEvent.click(button);

    await waitFor(() =>
      expect(toastInfo).toHaveBeenCalledWith(
        expect.stringMatching(/hasn't published yet/i),
      ),
    );
    expect(clickSpy).not.toHaveBeenCalled();

    clickSpy.mockRestore();
  });

  it("surfaces a genuine fetch error via a toast instead of throwing unhandled", async () => {
    getCertificate.mockRejectedValue(new Error("network drop"));

    render(withProviders(<ReleaseProposalCard />));
    const button = await screen.findByRole("button", {
      name: /Download certificate/i,
    });
    await userEvent.click(button);

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        expect.stringMatching(/download failed/i),
      ),
    );
  });
});
