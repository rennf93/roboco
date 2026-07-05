import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { VideoPost } from "@/lib/api/video";

const { resolveApproveRef } = vi.hoisted(() => ({
  resolveApproveRef: { current: null as null | ((v: unknown) => void) },
}));

const { listPosts, approve, reject, requestVideo, videoMediaUrl } = vi.hoisted(
  () => ({
    listPosts: vi.fn(
      async () =>
        [
          {
            task_id: "v-1",
            source: "video_post",
            title: "Video: release v0.19.0",
            status: "pending",
            occasion: "release",
            script: "RoboCo v0.19.0 just shipped!",
            platforms: ["x", "tiktok"],
            x_caption: "RoboCo v0.19.0 is here!",
            tiktok_caption: "New RoboCo drop!",
          },
        ] as VideoPost[],
    ),
    // Deferred so the test can freeze the approve mid-flight.
    approve: vi.fn(
      () =>
        new Promise((r) => {
          resolveApproveRef.current = r as (v: unknown) => void;
        }),
    ),
    reject: vi.fn(async () => ({})),
    requestVideo: vi.fn(async () => ({
      status: "opened",
      task_id: "v-2",
      detail: "Video-authoring task opened.",
    })),
    videoMediaUrl: vi.fn(
      (taskId: string, cut: string) => `/api/video/posts/${taskId}/media?cut=${cut}`,
    ),
  }),
);

vi.mock("@/lib/api", () => ({
  videoApi: { listPosts, approve, reject, requestVideo },
  videoMediaUrl,
}));

import { VideoPostQueue } from "../video-post-queue";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("VideoPostQueue", () => {
  beforeEach(() => {
    listPosts.mockClear();
    approve.mockClear();
    reject.mockClear();
    requestVideo.mockClear();
    resolveApproveRef.current = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders a draft with its occasion badge and both platform captions", async () => {
    render(withQueryClient(<VideoPostQueue />));
    expect(await screen.findByText("release")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("RoboCo v0.19.0 is here!"),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("New RoboCo drop!")).toBeInTheDocument();
  });

  it("switches the preview src between the 9:16 and 1:1 cuts", async () => {
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");

    const video = document.querySelector("video");
    expect(video?.getAttribute("src")).toContain("cut=vertical");

    fireEvent.click(screen.getByRole("button", { name: "1:1" }));
    expect(document.querySelector("video")?.getAttribute("src")).toContain(
      "cut=square",
    );
  });

  it("disables Approve when the edited X caption exceeds 280 characters", async () => {
    render(withQueryClient(<VideoPostQueue />));
    const textarea = await screen.findByDisplayValue("RoboCo v0.19.0 is here!");
    fireEvent.change(textarea, { target: { value: "x".repeat(281) } });

    expect(screen.getByText("281/280")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Approve/ }),
    ).toBeDisabled();
  });

  it("only sends captions for platforms left toggled on", async () => {
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");

    // Un-toggle TikTok — its stored caption should NOT be sent on approve.
    fireEvent.click(screen.getByLabelText("Edit TikTok caption"));
    fireEvent.click(screen.getByRole("button", { name: /Approve/ }));

    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith("v-1", {
        x_caption: "RoboCo v0.19.0 is here!",
      }),
    );
    resolveApproveRef.current?.({ status: "posted", posted: {}, detail: "ok" });
  });

  it("rejects a draft with a reason", async () => {
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    const reasonBox = await screen.findByLabelText("Reason");
    fireEvent.change(reasonBox, { target: { value: "wrong occasion" } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(reject).toHaveBeenCalledWith("v-1", "wrong occasion"),
    );
  });

  it("requests an on-demand video with the chosen occasion, brief, and platforms", async () => {
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");

    fireEvent.click(screen.getByRole("button", { name: /Request a video/ }));
    fireEvent.change(screen.getByLabelText("Occasion"), {
      target: { value: "Founder's Day" },
    });
    fireEvent.change(screen.getByLabelText("Brief"), {
      target: { value: "Celebrate the founding." },
    });
    // Both platforms are checked by default — leave as-is and submit.
    fireEvent.click(screen.getByRole("button", { name: "Request" }));

    await waitFor(() =>
      expect(requestVideo).toHaveBeenCalledWith({
        occasion: "Founder's Day",
        brief: "Celebrate the founding.",
        platforms: ["x", "tiktok"],
      }),
    );
  });

  it("shows an empty-state card (with the request action) when there are no drafts", async () => {
    listPosts.mockResolvedValueOnce([]);
    render(withQueryClient(<VideoPostQueue />));
    expect(await screen.findByText(/No drafts yet/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Request a video/ }),
    ).toBeInTheDocument();
  });
});
