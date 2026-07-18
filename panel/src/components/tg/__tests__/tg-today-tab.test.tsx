import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TgTodayTab } from "../tg-today-tab";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ default: { get } }));

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
    fleet: { total: 3, by_status: { active: 3 }, working: [] },
    spend: { tokens_today: 1_234_000, cost_today_usd: 12.34 },
    ship: { version: "0.25.0", open_release_proposal: false, ci_fix_tasks: 0 },
    ...overrides,
  };
}

describe("TgTodayTab", () => {
  // Block body on purpose: returning the mock from beforeEach would make
  // vitest call it as an after-test teardown hook.
  beforeEach(() => {
    get.mockReset();
  });

  it("shows skeletons while loading", () => {
    get.mockReturnValue(new Promise(() => {}));
    renderTab();
    expect(document.querySelectorAll("[data-slot=skeleton]").length).toBeGreaterThan(
      0,
    );
  });

  it("renders the all-clear state and the spend/ship numbers", async () => {
    get.mockResolvedValue({ data: brief() });
    renderTab();

    expect(await screen.findByText(/all clear/i)).toBeInTheDocument();
    expect(screen.getByText("$12.34")).toBeInTheDocument();
    expect(screen.getByText(/1\.2M tokens/)).toBeInTheDocument();
    expect(screen.getByText("v0.25.0")).toBeInTheDocument();
    expect(screen.getByText(/no release pending/i)).toBeInTheDocument();
    expect(screen.getByText("3 agents")).toBeInTheDocument();
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

  it("shows an error state when the brief fails to load", async () => {
    get.mockRejectedValue(new Error("boom"));
    renderTab();
    await waitFor(() =>
      expect(screen.getByText(/couldn.t load/i)).toBeInTheDocument(),
    );
  });
});
