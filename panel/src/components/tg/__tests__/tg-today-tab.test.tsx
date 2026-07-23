import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TgTodayTab } from "../tg-today-tab";

const { get, ackMock, notifItems } = vi.hoisted(() => ({
  get: vi.fn(),
  ackMock: vi.fn(),
  notifItems: { current: [] as Array<Record<string, unknown>> },
}));
vi.mock("@/lib/api/client", () => ({
  default: { get },
  getErrorMessage: () => "error",
}));
vi.mock("@/hooks/use-notifications", () => ({
  notificationKeys: { all: ["notifications"] },
  useNotifications: () => ({ data: { items: notifItems.current } }),
}));
vi.mock("@/lib/api/notifications", () => ({
  notificationsApi: { acknowledge: ackMock },
}));

function renderTab(onNavigate = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <TgTodayTab onNavigate={onNavigate} />
    </QueryClientProvider>,
  );
  return onNavigate;
}

function brief(overrides: Record<string, unknown> = {}) {
  return {
    needs_you: {
      total: 0,
      awaiting_ceo_count: 0,
      awaiting_ceo: [],
      blocked_count: 0,
      blocked: [],
      held_drafts: {
        release_proposals: 0,
        x_posts: 0,
        video_posts: 0,
        roadmap_items: 0,
      },
    },
    fleet: { total: 3, by_status: { active: 3, idle: 0 }, working: [] },
    spend: {
      tokens_today: 1_234_000,
      cost_today_usd: 12.34,
      series: [1, 2, 3, 4, 5, 6, 12.34],
      delta_pct: 10,
    },
    velocity: { series: [1, 2, 0, 3, 1, 4, 2], week_total: 13 },
    ship: { version: "0.25.0", open_release_proposal: false, ci_fix_tasks: 0 },
    ...overrides,
  };
}

describe("TgTodayTab", () => {
  // Block body on purpose: returning the mock from beforeEach would make
  // vitest call it as an after-test teardown hook.
  beforeEach(() => {
    get.mockReset();
    ackMock.mockReset();
    notifItems.current = [];
    // Reduced motion → the spend count-up lands instantly; these tests
    // assert content, not animation timing.
    window.matchMedia = ((query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
  });

  it("shows skeletons while loading", () => {
    get.mockReturnValue(new Promise(() => {}));
    renderTab();
    expect(
      document.querySelectorAll("[data-slot=skeleton]").length,
    ).toBeGreaterThan(0);
  });

  it("renders the all-clear state and the spend/ship numbers", async () => {
    get.mockResolvedValue({ data: brief() });
    renderTab();

    expect(await screen.findByText(/all clear/i)).toBeInTheDocument();
    // The spend hero counts up to the target, so wait for the final frame.
    expect(await screen.findByText("$12.34")).toBeInTheDocument();
    expect(screen.getByText(/1\.2M tokens/)).toBeInTheDocument();
    expect(screen.getByText("v0.25.0")).toBeInTheDocument();
    expect(screen.getByText(/no release pending/i)).toBeInTheDocument();
    expect(screen.getByText(/3 active/)).toBeInTheDocument();
  });

  it("renders needs-you items and deep-links taps into the right tab", async () => {
    get.mockResolvedValue({
      data: brief({
        needs_you: {
          total: 3,
          awaiting_ceo_count: 1,
          awaiting_ceo: [
            {
              id: "t1",
              title: "Root PR ready",
              status: "awaiting_ceo_approval",
              team: "backend",
              updated_at: null,
            },
          ],
          blocked_count: 0,
          blocked: [],
          held_drafts: {
            release_proposals: 1,
            x_posts: 1,
            video_posts: 0,
            roadmap_items: 0,
          },
        },
      }),
    });
    const onNavigate = vi.fn();
    renderTab(onNavigate);

    await userEvent.click(await screen.findByText(/Release · 1/));
    expect(onNavigate).toHaveBeenCalledWith("approvals");

    await userEvent.click(screen.getByText("Root PR ready"));
    expect(onNavigate).toHaveBeenCalledWith("board");
  });

  it("renders the operations ring and deep-links Ship into the release", async () => {
    get.mockResolvedValue({
      data: brief({
        ship: {
          version: "0.25.0",
          open_release_proposal: true,
          ci_fix_tasks: 0,
        },
      }),
    });
    const onNavigate = vi.fn();
    renderTab(onNavigate);

    await userEvent.click(await screen.findByRole("button", { name: /ship/i }));
    expect(onNavigate).toHaveBeenCalledWith("approvals", "release");
    expect(screen.getByRole("button", { name: /sweep/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /fleet/i })).toBeInTheDocument();
  });

  it("ack-all acknowledges every pending notification", async () => {
    get.mockResolvedValue({ data: brief() });
    notifItems.current = [
      { id: "n1", requires_ack: true, is_acknowledged: false },
      { id: "n2", requires_ack: true, is_acknowledged: false },
      { id: "n3", requires_ack: false, is_acknowledged: false },
    ];
    ackMock.mockResolvedValue({});
    renderTab();

    await userEvent.click(
      await screen.findByRole("button", { name: /ack all/i }),
    );
    await waitFor(() => expect(ackMock).toHaveBeenCalledTimes(2));
    expect(ackMock).toHaveBeenCalledWith("n1");
    expect(ackMock).toHaveBeenCalledWith("n2");
  });

  it("opens the fleet sheet with the full working roster", async () => {
    get.mockResolvedValue({
      data: brief({
        fleet: {
          total: 26,
          by_status: { active: 5, idle: 21 },
          working: [
            {
              name: "be-dev-1",
              role: "developer",
              team: "backend",
              task_title: "Task A",
            },
            {
              name: "be-dev-2",
              role: "developer",
              team: "backend",
              task_title: "Task B",
            },
            {
              name: "fe-dev-1",
              role: "developer",
              team: "frontend",
              task_title: "Task C",
            },
            {
              name: "fe-qa",
              role: "qa",
              team: "frontend",
              task_title: "Task D",
            },
            {
              name: "ux-dev-1",
              role: "developer",
              team: "ux_ui",
              task_title: "Task E",
            },
          ],
        },
      }),
    });
    renderTab();

    // The section previews 3 of 5; tapping it opens the full-roster sheet.
    await userEvent.click(
      await screen.findByText(/\+2 more · tap for the full roster/),
    );
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Task E")).toBeInTheDocument();
    expect(screen.getByText(/idle · 21/)).toBeInTheDocument();
  });

  it("labels an untracked subscription-billed spend day instead of a bare $0", async () => {
    get.mockResolvedValue({
      data: brief({
        spend: {
          tokens_today: 456_221,
          cost_today_usd: 0,
          subscription_billed: true,
          series: [1, 2, 3, 4, 5, 6, 0],
          delta_pct: null,
        },
      }),
    });
    renderTab();

    expect(await screen.findByText("≈$0")).toBeInTheDocument();
    expect(screen.getByText(/subscription \(untracked\)/i)).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("shows an error state when the brief fails to load", async () => {
    get.mockRejectedValue(new Error("boom"));
    renderTab();
    await waitFor(() =>
      expect(screen.getByText(/couldn.t load/i)).toBeInTheDocument(),
    );
  });
});
