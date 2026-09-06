import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TgApprovalsTab } from "../tg-approvals-tab";
import { toast } from "sonner";

const {
  releaseApi,
  xApi,
  videoApi,
  roadmapApi,
  pestControlApi,
  spackleApi,
  scalesApi,
  dogfoodApi,
} = vi.hoisted(() => ({
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
  pestControlApi: {
    listCycles: vi.fn(),
    approveItem: vi.fn(),
    rejectItem: vi.fn(),
  },
  spackleApi: {
    listCycles: vi.fn(),
    approveItem: vi.fn(),
    rejectItem: vi.fn(),
  },
  scalesApi: {
    listCycles: vi.fn(),
    approveItem: vi.fn(),
    rejectItem: vi.fn(),
  },
  dogfoodApi: {
    listCycles: vi.fn(),
    approveItem: vi.fn(),
    rejectItem: vi.fn(),
  },
}));
vi.mock("@/lib/api/release", () => ({ releaseApi }));
vi.mock("@/lib/api/x", () => ({ xApi }));
vi.mock("@/lib/api/video", () => ({ videoApi }));
vi.mock("@/lib/api/roadmap", () => ({ roadmapApi }));
vi.mock("@/lib/api/pest-control", () => ({ pestControlApi }));
vi.mock("@/lib/api/spackle", () => ({ spackleApi }));
vi.mock("@/lib/api/scales", () => ({ scalesApi }));
vi.mock("@/lib/api/dogfood", () => ({ dogfoodApi }));
vi.mock("@/lib/api/client", () => ({
  getErrorMessage: (err: unknown) =>
    (err as { message?: string } | undefined)?.message ?? "Unknown error",
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
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

// The three evidence-backed board-program queues share one item shape —
// exercised through Pest Control; Spackle/Dogfood ride the same detail.
function pestHuntCycle() {
  return {
    task_id: "pest-1",
    title: "Pest Control hunt",
    status: "pending",
    items: [
      {
        id: "pest-item-0",
        title: "Escalation DMs leak stale task ids",
        description: "Blocker escalations reference cancelled tasks.",
        acceptance_criteria: ["skip cancelled targets"],
        project_slug: "roboco",
        team: "backend",
        priority: 1,
        evidence: "Three rework clusters cite stale escalation text.",
        status: "proposed",
      },
    ],
  };
}

function rebalanceCycle() {
  return {
    task_id: "scales-1",
    title: "Rebalance",
    status: "pending",
    items: [
      {
        id: "scales-item-0",
        task_ref: "demo-t7",
        target_task_id: "demo-t7",
        target_task_title: "Self-serve workspace invites",
        action: "reprioritize",
        new_priority: 0,
        rationale: "Two enterprise trials asked for it.",
        status: "proposed",
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
    pestControlApi.listCycles.mockResolvedValue([]);
    spackleApi.listCycles.mockResolvedValue([]);
    scalesApi.listCycles.mockResolvedValue([]);
    dogfoodApi.listCycles.mockResolvedValue([]);
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

  it("lists only proposed board-program items, from all four program queues", async () => {
    pestControlApi.listCycles.mockResolvedValue([pestHuntCycle()]);
    spackleApi.listCycles.mockResolvedValue([
      {
        task_id: "spackle-1",
        title: "Spackle audit",
        status: "pending",
        items: [
          {
            id: "spackle-item-0",
            title: "Docs-divergence flag has no docs entry",
            description: "d",
            acceptance_criteria: [],
            project_slug: "roboco",
            team: "backend",
            priority: 2,
            evidence: "Flag armed, zero doc hits.",
            status: "proposed",
          },
        ],
      },
    ]);
    scalesApi.listCycles.mockResolvedValue([rebalanceCycle()]);
    dogfoodApi.listCycles.mockResolvedValue([
      {
        task_id: "dogfood-1",
        title: "Dogfood walk",
        status: "pending",
        items: [
          {
            id: "dogfood-item-0",
            title: "Task detail tab bar is dead on narrow screens",
            description: "d",
            acceptance_criteria: [],
            project_slug: "roboco",
            team: "frontend",
            priority: 2,
            evidence: "Overflow tabs clipped on a phone viewport.",
            status: "proposed",
          },
        ],
      },
    ]);

    renderTab();

    expect(
      await screen.findByText("Escalation DMs leak stale task ids"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Docs-divergence flag has no docs entry"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Self-serve workspace invites"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Task detail tab bar is dead on narrow screens"),
    ).toBeInTheDocument();
    // A decided item never renders; one pending item is not a clear queue.
    expect(screen.queryByText(/queue is clear/i)).not.toBeInTheDocument();
  });

  it("rejects a pest-control item with a reason on the shared endpoint", async () => {
    pestControlApi.listCycles.mockResolvedValue([pestHuntCycle()]);
    pestControlApi.rejectItem.mockResolvedValue({
      status: "rejected",
      item_id: "pest-item-0",
      detail: "ok",
    });

    renderTab();
    await userEvent.click(
      await screen.findByText("Escalation DMs leak stale task ids"),
    );
    await userEvent.click(screen.getByRole("button", { name: /reject…/i }));

    const submit = screen.getByRole("button", { name: /^reject$/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "already fixed");
    await userEvent.click(submit);

    await waitFor(() =>
      expect(pestControlApi.rejectItem).toHaveBeenCalledWith(
        "pest-1",
        "pest-item-0",
        "already fixed",
      ),
    );
  });

  it("approves a scales rebalance item", async () => {
    scalesApi.listCycles.mockResolvedValue([rebalanceCycle()]);
    scalesApi.approveItem.mockResolvedValue({
      status: "approved",
      item_id: "scales-item-0",
      detail: "Priority set to P0",
    });

    renderTab();
    await userEvent.click(
      await screen.findByText("Self-serve workspace invites"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /approve → apply/i }),
    );

    await waitFor(() =>
      expect(scalesApi.approveItem).toHaveBeenCalledWith(
        "scales-1",
        "scales-item-0",
      ),
    );
  });

  it("approves a pest-control item into the backlog", async () => {
    pestControlApi.listCycles.mockResolvedValue([pestHuntCycle()]);
    pestControlApi.approveItem.mockResolvedValue({
      status: "approved",
      item_id: "pest-item-0",
      detail: "ok",
    });

    renderTab();
    await userEvent.click(
      await screen.findByText("Escalation DMs leak stale task ids"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /approve → backlog/i }),
    );

    await waitFor(() =>
      expect(pestControlApi.approveItem).toHaveBeenCalledWith(
        "pest-1",
        "pest-item-0",
      ),
    );
  });

  it("rejects a scales rebalance item with a reason", async () => {
    scalesApi.listCycles.mockResolvedValue([rebalanceCycle()]);
    scalesApi.rejectItem.mockResolvedValue({
      status: "rejected",
      item_id: "scales-item-0",
      detail: "ok",
    });

    renderTab();
    await userEvent.click(
      await screen.findByText("Self-serve workspace invites"),
    );
    await userEvent.click(screen.getByRole("button", { name: /reject…/i }));

    await userEvent.type(screen.getByRole("textbox"), "still needed");
    await userEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    await waitFor(() =>
      expect(scalesApi.rejectItem).toHaveBeenCalledWith(
        "scales-1",
        "scales-item-0",
        "still needed",
      ),
    );
  });

  it("keeps the detail open when a pest approve comes back not approved", async () => {
    pestControlApi.listCycles.mockResolvedValue([pestHuntCycle()]);
    pestControlApi.approveItem.mockResolvedValue({
      status: "rejected",
      item_id: "pest-item-0",
      detail: "Another CEO decision won the race",
    });

    renderTab();
    await userEvent.click(
      await screen.findByText("Escalation DMs leak stale task ids"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /approve → backlog/i }),
    );

    await waitFor(() =>
      expect(pestControlApi.approveItem).toHaveBeenCalledWith(
        "pest-1",
        "pest-item-0",
      ),
    );
    // A non-approved status never pops back to the list.
    expect(
      screen.getByRole("button", { name: /approve → backlog/i }),
    ).toBeInTheDocument();
  });

  it("surfaces an approve failure and stays on the detail", async () => {
    pestControlApi.listCycles.mockResolvedValue([pestHuntCycle()]);
    pestControlApi.approveItem.mockRejectedValue(new Error("gateway down"));

    renderTab();
    await userEvent.click(
      await screen.findByText("Escalation DMs leak stale task ids"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /approve → backlog/i }),
    );

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("gateway down"),
    );
    expect(
      screen.getByRole("button", { name: /approve → backlog/i }),
    ).toBeInTheDocument();
  });

  it("surfaces a scales approve failure and stays on the detail", async () => {
    scalesApi.listCycles.mockResolvedValue([rebalanceCycle()]);
    scalesApi.approveItem.mockRejectedValue(new Error("gateway down"));

    renderTab();
    await userEvent.click(
      await screen.findByText("Self-serve workspace invites"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /approve → apply/i }),
    );

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("gateway down"),
    );
    expect(
      screen.getByRole("button", { name: /approve → apply/i }),
    ).toBeInTheDocument();
  });

  it("surfaces a scales reject failure and stays on the detail", async () => {
    scalesApi.listCycles.mockResolvedValue([rebalanceCycle()]);
    scalesApi.rejectItem.mockRejectedValue(new Error("gateway down"));

    renderTab();
    await userEvent.click(
      await screen.findByText("Self-serve workspace invites"),
    );
    await userEvent.click(screen.getByRole("button", { name: /reject…/i }));

    await userEvent.type(screen.getByRole("textbox"), "still needed");
    await userEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("gateway down"),
    );
    // The open reject form stays up — nothing was actioned.
    expect(screen.getByRole("button", { name: /^reject$/i })).toBeEnabled();
  });

  it("surfaces a pest reject failure and stays on the detail", async () => {
    pestControlApi.listCycles.mockResolvedValue([pestHuntCycle()]);
    pestControlApi.rejectItem.mockRejectedValue(new Error("gateway down"));

    renderTab();
    await userEvent.click(
      await screen.findByText("Escalation DMs leak stale task ids"),
    );
    await userEvent.click(screen.getByRole("button", { name: /reject…/i }));

    await userEvent.type(screen.getByRole("textbox"), "still broken");
    await userEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("gateway down"),
    );
    expect(screen.getByRole("button", { name: /^reject$/i })).toBeEnabled();
  });

  it("keeps the detail open when a scales approve comes back not approved", async () => {
    scalesApi.listCycles.mockResolvedValue([rebalanceCycle()]);
    scalesApi.approveItem.mockResolvedValue({
      status: "not_applied",
      item_id: "scales-item-0",
      detail: "Another CEO decision won the race",
    });

    renderTab();
    await userEvent.click(
      await screen.findByText("Self-serve workspace invites"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /approve → apply/i }),
    );

    await waitFor(() =>
      expect(scalesApi.approveItem).toHaveBeenCalledWith(
        "scales-1",
        "scales-item-0",
      ),
    );
    // A non-approved status never pops back to the list.
    expect(
      screen.getByRole("button", { name: /approve → apply/i }),
    ).toBeInTheDocument();
    expect(toast.warning).toHaveBeenCalledWith(
      "Another CEO decision won the race",
    );
  });

  it("drives a spackle item through the same shared detail", async () => {
    spackleApi.listCycles.mockResolvedValue([
      {
        task_id: "spackle-1",
        title: "Spackle audit",
        status: "pending",
        items: [
          {
            id: "spackle-item-0",
            title: "Docs-divergence flag has no docs entry",
            description: "Flag armed, zero doc hits.",
            acceptance_criteria: [],
            project_slug: "roboco",
            team: "backend",
            priority: 2,
            evidence: "Flag armed, zero doc hits.",
            status: "proposed",
          },
        ],
      },
    ]);
    spackleApi.approveItem.mockResolvedValue({
      status: "approved",
      item_id: "spackle-item-0",
      detail: "ok",
    });

    renderTab();
    await userEvent.click(
      await screen.findByText("Docs-divergence flag has no docs entry"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /approve → backlog/i }),
    );

    await waitFor(() =>
      expect(spackleApi.approveItem).toHaveBeenCalledWith(
        "spackle-1",
        "spackle-item-0",
      ),
    );
    expect(toast.success).toHaveBeenCalled();
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

  it("demo fixtures carry a proposed item per board-program queue", async () => {
    const d = await import("@/lib/telegram/demo-data");
    for (const source of [
      d.DEMO_PEST_CONTROL,
      d.DEMO_SPACKLE,
      d.DEMO_SCALES,
      d.DEMO_DOGFOOD,
    ]) {
      expect(source.length).toBeGreaterThan(0);
      expect(source[0].items.some((i) => i.status === "proposed")).toBe(true);
    }
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
