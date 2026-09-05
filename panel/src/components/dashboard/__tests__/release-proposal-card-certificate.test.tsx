import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReleaseCertificate, ReleaseProposal } from "@/lib/api/release";
import type { Task } from "@/types";
import { TaskStatus } from "@/types";

// Covers the "Download certificate" button (bfb48210): the happy-path
// download trigger and the 404-to-null toast path. Unlike the sibling
// release-proposal-card.test.tsx, react-query itself is NOT mocked here —
// only releaseApi/tasksApi — so the real useMutation/useQuery onSuccess
// callbacks run.
const { getProposal, getCertificate, approve, getTask } = vi.hoisted(() => ({
  getProposal: vi.fn(),
  getCertificate: vi.fn(),
  approve: vi.fn(),
  getTask: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  releaseApi: { getProposal, approve, reject: vi.fn(), getCertificate },
  tasksApi: { get: getTask },
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

const _POINTER_KEY = "roboco.release-certificate-pointer";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Cut v0.14.0",
    status: TaskStatus.AWAITING_CEO_APPROVAL,
    ...overrides,
  } as unknown as Task;
}

// Covers the real POST /release/proposal/approve contract: a 202-dispatch
// route that always returns {status: "accepted", version: ""} synchronously
// — the real publish runs ~40min later in a background task. Never assert a
// "published" state directly off the approve response (round-1's bug).
describe("ReleaseProposalCard — approve dispatch (real 202 contract)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getProposal.mockReset();
    getProposal.mockResolvedValue(buildProposal());
    getTask.mockReset();
    getTask.mockResolvedValue(buildTask());
    getCertificate.mockReset();
    approve.mockReset();
    toastInfo.mockClear();
    toastError.mockClear();
    toastSuccess.mockClear();
  });

  it("dispatches the executor and shows the background-dispatch toast, never a published state", async () => {
    approve.mockResolvedValue({
      status: "accepted",
      version: "",
      files_changed: [],
      commit_sha: null,
      release_url: null,
      detail: "dispatched",
    });

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
    await waitFor(() =>
      expect(toastInfo).toHaveBeenCalledWith(
        expect.stringMatching(/dispatched — running in the background/i),
      ),
    );
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(screen.queryByText(/^Published v/)).not.toBeInTheDocument();
  });
});

// Covers task 13af9490 + the round-2 pr_gate bounce: the old open-proposal
// "Download certificate" button was structurally unreachable (the proposal
// unmounts the instant it publishes, so its target version never coincides
// with a servable one) and the round-1 fix keyed a replacement off the
// approve response, which can never report "published" in production. The
// fix instead persists {taskId, version} to localStorage and confirms
// publication by polling GET /tasks/{taskId} for status === "completed" —
// re-confirmed against the server on every mount so the control survives a
// reload/navigation during the ~40min background publish.
describe("ReleaseProposalCard — Download certificate (task-status-confirmed publish)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getProposal.mockReset();
    getTask.mockReset();
    getCertificate.mockReset();
    toastInfo.mockClear();
    toastError.mockClear();
    toastSuccess.mockClear();
    globalThis.URL.createObjectURL = vi.fn(() => "blob:mock-url");
    globalThis.URL.revokeObjectURL = vi.fn();
  });

  it("renders no Download certificate control while the proposal's task hasn't completed — only one control ever exists", async () => {
    getProposal.mockResolvedValue(buildProposal());
    getTask.mockResolvedValue(
      buildTask({ id: "t1", status: TaskStatus.AWAITING_CEO_APPROVAL }),
    );

    render(withProviders(<ReleaseProposalCard />));
    await screen.findByRole("button", { name: /^Approve & publish$/i });

    expect(
      screen.queryByRole("button", { name: /Download certificate/i }),
    ).not.toBeInTheDocument();
  });

  it("renders exactly one Download certificate control once the stored task is confirmed completed, targeting the stored version — survives the open-proposal query going to null", async () => {
    // Simulates a reload: a pointer already sits in localStorage from a
    // prior mount, and the open-proposal query 404s to null now that the
    // proposal it was drawn from has completed.
    window.localStorage.setItem(
      _POINTER_KEY,
      JSON.stringify({ taskId: "t9", version: "0.16.0" }),
    );
    getProposal.mockResolvedValue(null);
    getTask.mockResolvedValue(
      buildTask({ id: "t9", status: TaskStatus.COMPLETED }),
    );
    getCertificate.mockResolvedValue(buildCertificate());
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    render(withProviders(<ReleaseProposalCard />));

    await waitFor(() => expect(getTask).toHaveBeenCalledWith("t9"));
    const buttons = await screen.findAllByRole("button", {
      name: /Download certificate/i,
    });
    expect(buttons).toHaveLength(1);

    await userEvent.click(buttons[0]);

    await waitFor(() => expect(getCertificate).toHaveBeenCalledWith("0.16.0"));
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(globalThis.URL.createObjectURL).toHaveBeenCalled();
    expect(toastInfo).not.toHaveBeenCalled();

    clickSpy.mockRestore();
  });

  it("shows an info toast instead of downloading when the certificate isn't ready yet (404-to-null)", async () => {
    window.localStorage.setItem(
      _POINTER_KEY,
      JSON.stringify({ taskId: "t9", version: "0.16.0" }),
    );
    getProposal.mockResolvedValue(null);
    getTask.mockResolvedValue(
      buildTask({ id: "t9", status: TaskStatus.COMPLETED }),
    );
    getCertificate.mockResolvedValue(null);

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
  });

  it("surfaces a genuine fetch error via a toast instead of throwing unhandled", async () => {
    window.localStorage.setItem(
      _POINTER_KEY,
      JSON.stringify({ taskId: "t9", version: "0.16.0" }),
    );
    getProposal.mockResolvedValue(null);
    getTask.mockResolvedValue(
      buildTask({ id: "t9", status: TaskStatus.COMPLETED }),
    );
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

  it("clears the stored pointer once the task reaches a terminal non-completed status (cancelled)", async () => {
    window.localStorage.setItem(
      _POINTER_KEY,
      JSON.stringify({ taskId: "t9", version: "0.16.0" }),
    );
    getProposal.mockResolvedValue(null);
    getTask.mockResolvedValue(
      buildTask({ id: "t9", status: TaskStatus.CANCELLED }),
    );

    render(withProviders(<ReleaseProposalCard />));

    await waitFor(() => expect(getTask).toHaveBeenCalledWith("t9"));
    await waitFor(() =>
      expect(window.localStorage.getItem(_POINTER_KEY)).toBeNull(),
    );
    expect(
      screen.queryByRole("button", { name: /Download certificate/i }),
    ).not.toBeInTheDocument();
  });
});
