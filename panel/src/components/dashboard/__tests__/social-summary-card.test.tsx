import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { XPost } from "@/lib/api/x";
import type { VideoPost } from "@/lib/api/video";

const { listXPosts, listVideoPosts } = vi.hoisted(() => ({
  listXPosts: vi.fn(
    async () =>
      [
        {
          task_id: "x-1",
          source: "x_post",
          title: "X post",
          status: "pending",
          body: "body",
          char_count: 4,
        },
        {
          task_id: "x-2",
          source: "x_reply",
          title: "X reply",
          status: "pending",
          body: "body2",
          char_count: 5,
        },
      ] as XPost[],
  ),
  listVideoPosts: vi.fn(
    async () =>
      [
        {
          task_id: "v-1",
          source: "video_post",
          title: "Video",
          status: "pending",
          occasion: "release",
          script: "script",
          platforms: ["x"],
        },
      ] as VideoPost[],
  ),
}));

vi.mock("@/lib/api", () => ({
  xApi: { listPosts: listXPosts },
  videoApi: { listPosts: listVideoPosts },
}));

import { SocialSummaryCard } from "../social-summary-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("SocialSummaryCard", () => {
  beforeEach(() => {
    listXPosts.mockClear();
    listVideoPosts.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the pending draft counts for both X and video and a total badge", async () => {
    render(withQueryClient(<SocialSummaryCard />));
    expect(await screen.findByText("2 X drafts")).toBeInTheDocument();
    expect(screen.getByText("1 video draft")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("links to /social", async () => {
    render(withQueryClient(<SocialSummaryCard />));
    await screen.findByText("2 X drafts");
    const link = screen.getByRole("link", { name: /Open Social/ });
    expect(link).toHaveAttribute("href", "/social");
  });

  it("renders zero counts and no total badge when both queues are empty", async () => {
    listXPosts.mockResolvedValueOnce([]);
    listVideoPosts.mockResolvedValueOnce([]);
    render(withQueryClient(<SocialSummaryCard />));
    expect(await screen.findByText("0 X drafts")).toBeInTheDocument();
    expect(screen.getByText("0 video drafts")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});
