import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { XPostHistoryEntry } from "@/lib/api/x";
import type { VideoPostHistoryEntry } from "@/lib/api/video";

const { listXHistory, listVideoHistory } = vi.hoisted(() => ({
  listXHistory: vi.fn(
    async () =>
      [
        {
          task_id: "x-9",
          source: "x_post",
          title: "X post: release v0.16.0",
          status: "completed",
          body: "RoboCo v0.16.0 shipped!",
          char_count: 23,
          tweet_id: "555",
          acted_at: "2026-07-01T00:00:00Z",
        },
        {
          task_id: "x-10",
          source: "x_reply",
          title: "X reply: mention m2",
          status: "cancelled",
          body: "Not our voice",
          char_count: 13,
          reject_reason: "tone mismatch",
          acted_at: "2026-06-29T00:00:00Z",
        },
      ] as XPostHistoryEntry[],
  ),
  listVideoHistory: vi.fn(
    async () =>
      [
        {
          task_id: "v-9",
          source: "video_post",
          title: "Video: release v0.18.0",
          status: "completed",
          occasion: "release",
          script: "old script",
          platforms: ["x", "tiktok"],
          posted: { x: "xid777", tiktok: "tt-abc" },
          acted_at: "2026-06-30T00:00:00Z",
        },
      ] as VideoPostHistoryEntry[],
  ),
}));

vi.mock("@/lib/api", () => ({
  xApi: { listHistory: listXHistory },
  videoApi: { listHistory: listVideoHistory },
}));

import { SocialHistorySection } from "../social-history-section";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("SocialHistorySection", () => {
  beforeEach(() => {
    listXHistory.mockClear();
    listVideoHistory.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("is collapsed by default and fetches neither source", async () => {
    render(withQueryClient(<SocialHistorySection />));
    expect(await screen.findByText("History")).toBeInTheDocument();
    expect(listXHistory).not.toHaveBeenCalled();
    expect(listVideoHistory).not.toHaveBeenCalled();
    expect(screen.queryByText("tone mismatch")).not.toBeInTheDocument();
  });

  it("lazy-fetches both sources on expand and interleaves them newest-first", async () => {
    render(withQueryClient(<SocialHistorySection />));
    fireEvent.click(screen.getByRole("button", { name: /Show history/ }));

    await waitFor(() => expect(listXHistory).toHaveBeenCalledWith(50));
    await waitFor(() => expect(listVideoHistory).toHaveBeenCalledWith(50));

    expect(await screen.findByText("X post")).toBeInTheDocument();
    expect(screen.getByText("Video")).toBeInTheDocument();
    expect(screen.getByText("X reply")).toBeInTheDocument();

    // Newest acted_at first: x-9 (07-01) > video v-9 (06-30) > x-10 (06-29).
    const rows = screen.getAllByText(/^(X post|X reply|Video)$/);
    expect(rows.map((el) => el.textContent)).toEqual([
      "X post",
      "Video",
      "X reply",
    ]);
  });

  it("renders a posted X row linking the tweet and a rejected row with the reason", async () => {
    render(withQueryClient(<SocialHistorySection />));
    fireEvent.click(screen.getByRole("button", { name: /Show history/ }));

    await screen.findByText("X post");
    const link = screen.getByRole("link", { name: "View on X" });
    expect(link).toHaveAttribute("href", "https://x.com/i/status/555");
    expect(screen.getByText(/tone mismatch/)).toBeInTheDocument();
  });

  it("renders a posted video row with both platform ids (X links out, TikTok shows the raw id)", async () => {
    render(withQueryClient(<SocialHistorySection />));
    fireEvent.click(screen.getByRole("button", { name: /Show history/ }));

    await screen.findByText("Video");
    const videoLink = screen.getByRole("link", { name: /xid777/ });
    expect(videoLink).toHaveAttribute("href", "https://x.com/i/status/xid777");
    expect(screen.getByText(/tt-abc/)).toBeInTheDocument();
  });

  it("shows an empty state when neither source has acted-on drafts", async () => {
    listXHistory.mockResolvedValueOnce([]);
    listVideoHistory.mockResolvedValueOnce([]);
    render(withQueryClient(<SocialHistorySection />));
    fireEvent.click(screen.getByRole("button", { name: /Show history/ }));

    expect(
      await screen.findByText("No acted-on drafts yet."),
    ).toBeInTheDocument();
  });
});
