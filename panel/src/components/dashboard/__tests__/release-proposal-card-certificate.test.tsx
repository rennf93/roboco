import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type {
  ReleaseCertificate,
  ReleaseExecuteResult,
  ReleaseProposal,
} from "@/lib/api/release";

// Covers the "Download certificate" button (bfb48210): the happy-path
// download trigger and the 404-to-null toast path. Unlike the sibling
// release-proposal-card.test.tsx, react-query itself is NOT mocked here —
// only releaseApi — so the real useMutation onSuccess/onError callbacks run.
const { getProposal, getCertificate, approve } = vi.hoisted(() => ({
  getProposal: vi.fn(),
  getCertificate: vi.fn(),
  approve: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  releaseApi: { getProposal, approve, reject: vi.fn(), getCertificate },
}));

const { toastInfo, toastError, toastSuccess } = vi.hoisted(() => ({
  toastInfo: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    warning: vi.fn(),
    error: toastError,
    info: toastInfo,
  },
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
    approve.mockReset();
    toastInfo.mockClear();
    toastError.mockClear();
    toastSuccess.mockClear();
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
    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalledWith(
      "blob:mock-url",
    );
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

// Covers task 13af9490: the pr_gate bounce on PR #1022 found the button
// structurally unreachable — it lived only inside the open-proposal card
// (targeting report.proposed_version), but the certificate endpoint only
// ever serves a COMPLETED version, and getProposal() 404s to null the
// instant one publishes. The fix stashes {version, release_url} from the
// approve outcome so a "Download certificate" affordance survives the
// open-proposal query going to null.
describe("ReleaseProposalCard — post-publish reachability (13af9490)", () => {
  beforeEach(() => {
    getCertificate.mockReset();
    approve.mockReset();
    toastInfo.mockClear();
    toastError.mockClear();
    toastSuccess.mockClear();
    globalThis.URL.createObjectURL = vi.fn(() => "blob:mock-url");
    globalThis.URL.revokeObjectURL = vi.fn();
  });

  function buildPublishedResult(version: string): ReleaseExecuteResult {
    return {
      status: "published",
      version,
      files_changed: ["pyproject.toml"],
      commit_sha: "abc123",
      release_url: `https://github.com/example/repo/releases/tag/v${version}`,
      detail: "published",
    };
  }

  it("keeps Download certificate reachable after a publish, wired to the just-published version — not report.proposed_version from a stale open-proposal object", async () => {
    // Distinct from buildProposal()'s report.proposed_version ("0.14.0") so a
    // pass here can only mean the stored published version was used.
    const publishedVersion = "0.15.0";
    getProposal.mockReset();
    getProposal.mockResolvedValueOnce(buildProposal());
    // Mirrors the real /release/proposal 404-to-null once the proposal that
    // was open completes — the open-proposal card unmounts on this refetch.
    getProposal.mockResolvedValue(null);
    approve.mockResolvedValue(buildPublishedResult(publishedVersion));
    getCertificate.mockResolvedValue(buildCertificate());
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    render(withProviders(<ReleaseProposalCard />));

    const approveButton = await screen.findByRole("button", {
      name: /^Approve & publish$/i,
    });
    await userEvent.click(approveButton);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: /Approve & publish/i }),
    );
    await waitFor(() => expect(approve).toHaveBeenCalled());

    // The open-proposal card is gone (getProposal now resolves null), but a
    // persistent confirmation block stays mounted off the stored version.
    await waitFor(() =>
      expect(
        screen.getByText(new RegExp(`Published v${publishedVersion}`)),
      ).toBeInTheDocument(),
    );

    const downloadButton = await screen.findByRole("button", {
      name: /Download certificate/i,
    });
    await userEvent.click(downloadButton);

    await waitFor(() =>
      expect(getCertificate).toHaveBeenCalledWith(publishedVersion),
    );
    expect(getCertificate).not.toHaveBeenCalledWith(
      buildProposal().report.proposed_version,
    );

    clickSpy.mockRestore();
  });
});
