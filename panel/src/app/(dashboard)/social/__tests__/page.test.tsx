import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { XPostHistoryEntry } from "@/lib/api/x";
import type { VideoPostHistoryEntry } from "@/lib/api/video";

// The queues have their own dedicated test files (x-post-queue.test.tsx,
// video-post-queue.test.tsx) — stub them here so this page test only checks
// composition + the unified history, not their internals.
vi.mock("@/components/dashboard/x-post-queue", () => ({
  XPostQueue: () => <div>XPostQueueStub</div>,
}));
vi.mock("@/components/dashboard/video-post-queue", () => ({
  VideoPostQueue: () => <div>VideoPostQueueStub</div>,
}));

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
      ] as XPostHistoryEntry[],
  ),
  listVideoHistory: vi.fn(
    async () =>
      [
        {
          task_id: "v-9",
          source: "video_post",
          title: "Video: spotlight",
          status: "cancelled",
          occasion: "spotlight",
          script: "script",
          platforms: ["tiktok"],
          posted: {},
          reject_reason: "off-brand",
          acted_at: "2026-06-30T00:00:00Z",
        },
      ] as VideoPostHistoryEntry[],
  ),
}));

vi.mock("@/lib/api", () => ({
  xApi: { listHistory: listXHistory },
  videoApi: { listHistory: listVideoHistory },
}));

import SocialPage from "../page";

function withQueryClient(
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  }),
) {
  return { client };
}

describe("SocialPage", () => {
  beforeEach(() => {
    listXHistory.mockClear();
    listVideoHistory.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the page title and both queues", () => {
    const { client } = withQueryClient();
    render(
      <QueryClientProvider client={client}>
        <SocialPage />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("heading", { name: "Social" })).toBeInTheDocument();
    expect(screen.getByText("XPostQueueStub")).toBeInTheDocument();
    expect(screen.getByText("VideoPostQueueStub")).toBeInTheDocument();
  });

  it("renders the unified history collapsed by default, then shows posted + rejected rows on expand", async () => {
    const { client } = withQueryClient();
    render(
      <QueryClientProvider client={client}>
        <SocialPage />
      </QueryClientProvider>,
    );

    expect(screen.getByText("History")).toBeInTheDocument();
    expect(listXHistory).not.toHaveBeenCalled();
    expect(listVideoHistory).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Show history/ }));

    await waitFor(() => expect(listXHistory).toHaveBeenCalled());
    await waitFor(() => expect(listVideoHistory).toHaveBeenCalled());
    expect(await screen.findByText("Posted")).toBeInTheDocument();
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.getByText(/off-brand/)).toBeInTheDocument();
  });
});
