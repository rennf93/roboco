import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TgApprovalsTab } from "../tg-approvals-tab";

const { releaseApi, xApi, videoApi, roadmapApi } = vi.hoisted(() => ({
  releaseApi: { getProposal: vi.fn(), approve: vi.fn(), reject: vi.fn() },
  xApi: { listPosts: vi.fn(), approve: vi.fn(), reject: vi.fn() },
  videoApi: {
    listPosts: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    getMediaBlob: vi.fn(),
  },
  roadmapApi: {
    listCycles: vi.fn(),
    approveItem: vi.fn(),
    rejectItem: vi.fn(),
  },
}));
vi.mock("@/lib/api/release", () => ({ releaseApi }));
vi.mock("@/lib/api/x", () => ({ xApi }));
vi.mock("@/lib/api/video", () => ({ videoApi }));
vi.mock("@/lib/api/roadmap", () => ({ roadmapApi }));
vi.mock("@/lib/api/client", () => ({
  getErrorMessage: (err: unknown) =>
    (err as { message?: string } | undefined)?.message ?? "Unknown error",
}));

function xPost(body = "Shipped a thing.") {
  return {
    task_id: "x-1",
    source: "x_post",
    title: "Release post",
    status: "pending",
    body,
    char_count: body.length,
    release_version: "0.25.0",
  };
}

function roadmapCycle() {
  return {
    task_id: "cycle-1",
    title: "Cycle",
    status: "pending",
    goal: "Close friction",
    items: [
      {
        id: "item-0",
        title: "Better onboarding",
        description: "Make the first run smoother.",
        acceptance_criteria: ["one-command setup"],
        project_slug: "roboco",
        team: "backend",
        priority: 1,
        rationale: "Most churn happens on day one.",
        status: "proposed",
      },
      {
        id: "item-1",
        title: "Already decided",
        description: "d",
        acceptance_criteria: [],
        project_slug: "roboco",
        team: "backend",
        priority: 2,
        rationale: "r",
        status: "approved",
      },
    ],
  };
}

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <TgApprovalsTab />
    </QueryClientProvider>,
  );
}

describe("TgApprovalsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    releaseApi.getProposal.mockResolvedValue(null);
    xApi.listPosts.mockResolvedValue([]);
    videoApi.listPosts.mockResolvedValue([]);
    roadmapApi.listCycles.mockResolvedValue([]);
  });

  it("shows the clear state when every queue is empty", async () => {
    renderTab();
    expect(await screen.findByText(/queue is clear/i)).toBeInTheDocument();
  });

  it("lists items across queues — only proposed roadmap items count", async () => {
    xApi.listPosts.mockResolvedValue([xPost()]);
    roadmapApi.listCycles.mockResolvedValue([roadmapCycle()]);

    renderTab();

    expect(await screen.findByText("Shipped a thing.")).toBeInTheDocument();
    expect(screen.getByText("Better onboarding")).toBeInTheDocument();
    expect(screen.queryByText("Already decided")).not.toBeInTheDocument();
  });

  it("opens an X draft, edits it, and approves with the edited body", async () => {
    xApi.listPosts.mockResolvedValue([xPost("Original")]);
    xApi.approve.mockResolvedValue({ status: "posted", detail: "ok" });

    renderTab();
    await userEvent.click(await screen.findByText("Original"));

    const textarea = screen.getByRole("textbox");
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "Edited body");
    await userEvent.click(screen.getByRole("button", { name: /post to x/i }));

    await waitFor(() =>
      expect(xApi.approve).toHaveBeenCalledWith("x-1", "Edited body"),
    );
  });

  it("disables posting when the edit exceeds 280 characters", async () => {
    xApi.listPosts.mockResolvedValue([xPost("x".repeat(281))]);
    renderTab();
    await userEvent.click(await screen.findByText(/^x+$/));

    expect(screen.getByText("281 / 280")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /post to x/i })).toBeDisabled();
  });

  it("rejects a roadmap item with a reason and pops back to the list", async () => {
    roadmapApi.listCycles.mockResolvedValue([roadmapCycle()]);
    roadmapApi.rejectItem.mockResolvedValue({
      status: "rejected",
      item_id: "item-0",
      detail: "ok",
    });

    renderTab();
    await userEvent.click(await screen.findByText("Better onboarding"));
    await userEvent.click(screen.getByRole("button", { name: /reject…/i }));

    const submit = screen.getByRole("button", { name: /^reject$/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "not now");
    await userEvent.click(submit);

    await waitFor(() =>
      expect(roadmapApi.rejectItem).toHaveBeenCalledWith(
        "cycle-1",
        "item-0",
        "not now",
      ),
    );
  });

  it("back button in the detail returns to the list", async () => {
    xApi.listPosts.mockResolvedValue([xPost()]);
    renderTab();

    await userEvent.click(await screen.findByText("Shipped a thing."));
    expect(
      screen.getByRole("button", { name: /post to x/i }),
    ).toBeInTheDocument();

    // Outside Telegram there's no native BackButton — the visible fallback
    // arrow renders instead.
    const buttons = screen.getAllByRole("button");
    await userEvent.click(buttons[0]);
    expect(await screen.findByText("Shipped a thing.")).toBeInTheDocument();
  });
});
