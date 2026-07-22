import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { XPost } from "@/lib/api/x";

const { resolveApproveRef } = vi.hoisted(() => ({
  resolveApproveRef: { current: null as null | ((v: unknown) => void) },
}));

const { listPosts, approve, reject } = vi.hoisted(() => ({
  listPosts: vi.fn(
    async () =>
      [
        {
          task_id: "x-1",
          source: "x_post",
          title: "X post: release v0.17.0",
          status: "pending",
          body: "RoboCo v0.17.0 just shipped!",
          char_count: 28,
          release_version: "0.17.0",
        },
        {
          task_id: "x-2",
          source: "x_reply",
          title: "X reply: mention m1",
          status: "pending",
          body: "Thanks for the shoutout!",
          char_count: 24,
          mention: { id: "m1", author_id: "a1", text: "great work @roboco" },
        },
      ] as XPost[],
  ),
  // Deferred so the test can freeze the approve mid-flight.
  approve: vi.fn(
    () =>
      new Promise((r) => {
        resolveApproveRef.current = r as (v: unknown) => void;
      }),
  ),
  reject: vi.fn(async () => ({})),
}));

vi.mock("@/lib/api", () => ({ xApi: { listPosts, approve, reject } }));

const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock("sonner", () => ({ toast }));

import { XPostQueue } from "../x-post-queue";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("XPostQueue", () => {
  beforeEach(() => {
    listPosts.mockClear();
    approve.mockClear();
    reject.mockClear();
    toast.success.mockClear();
    toast.warning.mockClear();
    toast.error.mockClear();
    resolveApproveRef.current = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders both a release post and a mention reply draft", async () => {
    render(withQueryClient(<XPostQueue />));
    expect(await screen.findByText("Release post")).toBeInTheDocument();
    expect(await screen.findByText("Mention reply")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("RoboCo v0.17.0 just shipped!"),
    ).toBeInTheDocument();
  });

  it("renders a feature-spotlight draft with its own label and badge", async () => {
    listPosts.mockResolvedValueOnce([
      {
        task_id: "x-3",
        source: "x_feature",
        title: "X feature spotlight: playbooks",
        status: "pending",
        body: "Did you know RoboCo curates playbooks from real task runs?",
        char_count: 60,
        feature: { slug: "playbooks", title: "Playbook curation" },
      },
    ] as XPost[]);

    render(withQueryClient(<XPostQueue />));

    expect(await screen.findByText("Feature spotlight")).toBeInTheDocument();
    expect(screen.queryByText("Mention reply")).not.toBeInTheDocument();
    expect(screen.getByText(/Playbook curation/)).toBeInTheDocument();
  });

  it("renders a project badge when project_slug/project_name is present", async () => {
    listPosts.mockResolvedValueOnce([
      {
        task_id: "x-4",
        source: "x_post",
        title: "X post: release v0.18.0",
        status: "pending",
        body: "Acme Robotics v0.18.0 just shipped!",
        char_count: 36,
        release_version: "0.18.0",
        project_slug: "acme-robotics",
        project_name: "Acme Robotics",
      },
    ] as XPost[]);

    render(withQueryClient(<XPostQueue />));

    expect(await screen.findByText("Acme Robotics")).toBeInTheDocument();
  });

  it("disables only the row being approved, not every row's Approve", async () => {
    render(withQueryClient(<XPostQueue />));

    const approveButtons = await screen.findAllByRole("button", {
      name: /Approve/,
    });
    expect(approveButtons).toHaveLength(2);
    expect(approveButtons[0]).not.toBeDisabled();
    expect(approveButtons[1]).not.toBeDisabled();

    fireEvent.click(approveButtons[0]);
    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith(
        "x-1",
        "RoboCo v0.17.0 just shipped!",
      ),
    );

    await waitFor(() => expect(approveButtons[0]).toBeDisabled());
    expect(approveButtons[1]).not.toBeDisabled();

    resolveApproveRef.current?.({
      status: "posted",
      tweet_id: "1",
      detail: "ok",
    });
    await waitFor(() => expect(approveButtons[0]).not.toBeDisabled());
  });

  it("disables Approve when the edited body exceeds 280 characters", async () => {
    render(withQueryClient(<XPostQueue />));
    const textarea = await screen.findByDisplayValue(
      "RoboCo v0.17.0 just shipped!",
    );
    fireEvent.change(textarea, { target: { value: "x".repeat(281) } });

    const approveButtons = await screen.findAllByRole("button", {
      name: /Approve/,
    });
    expect(approveButtons[0]).toBeDisabled();
    expect(screen.getByText("281/280")).toBeInTheDocument();
  });

  it("rejects a draft with a reason", async () => {
    render(withQueryClient(<XPostQueue />));
    const rejectButtons = await screen.findAllByRole("button", {
      name: "Reject",
    });
    fireEvent.click(rejectButtons[1]);

    const reasonBox = await screen.findByLabelText("Reason");
    fireEvent.change(reasonBox, { target: { value: "not relevant" } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(reject).toHaveBeenCalledWith("x-2", "not relevant"),
    );
  });

  it("renders nothing when the queue is empty", async () => {
    listPosts.mockResolvedValueOnce([]);
    const { container } = render(withQueryClient(<XPostQueue />));
    await waitFor(() => expect(listPosts).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  // F(silent-bug-sweep #c): every XPostService.approve status must render a
  // distinct, non-swallowed toast — not a blanket success/failure.
  it.each([
    ["already_in_progress", "A post is already in progress for this draft."],
    [
      "no_credentials",
      "No X credentials configured — set them below first.",
    ],
    ["post_failed", "Posting failed: the X API rejected the tweet"],
    [
      "redis_unavailable",
      "Redis is unavailable — can't acquire the post lock.",
    ],
    ["already_posted", "Already posted — no-op."],
  ])("shows distinct feedback for the %s status", async (status, message) => {
    render(withQueryClient(<XPostQueue />));
    const approveButtons = await screen.findAllByRole("button", {
      name: /Approve/,
    });
    fireEvent.click(approveButtons[0]);
    await waitFor(() => expect(approve).toHaveBeenCalled());

    resolveApproveRef.current?.({
      status,
      tweet_id: null,
      detail: "the X API rejected the tweet",
    });

    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(message));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows a success toast for the posted status", async () => {
    render(withQueryClient(<XPostQueue />));
    const approveButtons = await screen.findAllByRole("button", {
      name: /Approve/,
    });
    fireEvent.click(approveButtons[0]);
    await waitFor(() => expect(approve).toHaveBeenCalled());

    resolveApproveRef.current?.({
      status: "posted",
      tweet_id: "1",
      detail: "ok",
    });

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Posted to X."),
    );
  });
});
