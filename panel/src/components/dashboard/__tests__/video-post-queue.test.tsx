import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { VideoPost } from "@/lib/api/video";

const { resolveApproveRef } = vi.hoisted(() => ({
  resolveApproveRef: { current: null as null | ((v: unknown) => void) },
}));

const { listPosts, approve, reject, requestVideo, getMediaBlob } = vi.hoisted(
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
    getMediaBlob: vi.fn(
      async () => new Blob(["fake-mp4-bytes"], { type: "video/mp4" }),
    ),
  }),
);

vi.mock("@/lib/api", () => ({
  videoApi: { listPosts, approve, reject, requestVideo, getMediaBlob },
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
    getMediaBlob.mockClear();
    resolveApproveRef.current = null;
    // jsdom has no Blob URL implementation. Distinct URLs per call so a
    // revoke can be asserted against the specific (stale) one it replaced.
    let objectUrlCount = 0;
    globalThis.URL.createObjectURL = vi.fn(
      () => `blob:mock-url-${++objectUrlCount}`,
    );
    globalThis.URL.revokeObjectURL = vi.fn();
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

  // H15: the 30s refetchInterval produces a new `post` prop, but useState
  // initializes once — so a server-side re-draft between the CEO opening the
  // card and approving would be silently overwritten by the stale initial
  // caption. The displayed value must track the server until the CEO edits.
  it("tracks the server caption until the CEO edits, then holds the edit (mirrors x-post-queue)", async () => {
    const basePost = {
      task_id: "v-1",
      source: "video_post",
      title: "Video: release v0.19.0",
      status: "pending",
      occasion: "release",
      script: "RoboCo v0.19.0 just shipped!",
      platforms: ["x", "tiktok"],
    };
    listPosts.mockResolvedValueOnce([
      { ...basePost, x_caption: "old", tiktok_caption: "old-tik" },
    ] as VideoPost[]);

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={client}>
        <VideoPostQueue />
      </QueryClientProvider>,
    );
    const xTextarea = await screen.findByDisplayValue("old");
    expect(xTextarea).toBeInTheDocument();

    // Simulate a 30s refetch producing a re-drafted server caption.
    listPosts.mockResolvedValueOnce([
      { ...basePost, x_caption: "new server text", tiktok_caption: "new-tik" },
    ] as VideoPost[]);
    await client.invalidateQueries({ queryKey: ["video", "posts"] });
    await waitFor(() =>
      expect(screen.getByDisplayValue("new server text")).toBeInTheDocument(),
    );

    // CEO types an edit — the derived value should now follow the user.
    fireEvent.change(screen.getByDisplayValue("new server text"), {
      target: { value: "my edit" },
    });
    expect(screen.getByDisplayValue("my edit")).toBeInTheDocument();

    // Another refetch with a newer server caption — the user's edit holds.
    listPosts.mockResolvedValueOnce([
      {
        ...basePost,
        x_caption: "even newer server text",
        tiktok_caption: "newer-tik",
      },
    ] as VideoPost[]);
    await client.invalidateQueries({ queryKey: ["video", "posts"] });
    await waitFor(() =>
      expect(screen.getByDisplayValue("my edit")).toBeInTheDocument(),
    );
    expect(
      screen.queryByDisplayValue("even newer server text"),
    ).not.toBeInTheDocument();
  });

  it("fetches the preview clip as a blob via axios and drives <video> off an object URL", async () => {
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");

    await waitFor(() =>
      expect(getMediaBlob).toHaveBeenCalledWith("v-1", "vertical"),
    );
    await waitFor(() =>
      expect(document.querySelector("video")?.getAttribute("src")).toBe(
        "blob:mock-url-1",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "1:1" }));
    await waitFor(() =>
      expect(getMediaBlob).toHaveBeenCalledWith("v-1", "square"),
    );
    await waitFor(() =>
      expect(document.querySelector("video")?.getAttribute("src")).toBe(
        "blob:mock-url-2",
      ),
    );
    // The stale cut's object URL is revoked once the new one takes over —
    // this is the leak-prevention path FIX 1 exists for.
    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalledWith(
      "blob:mock-url-1",
    );
  });

  it("disables Approve when the edited X caption exceeds 280 characters", async () => {
    render(withQueryClient(<VideoPostQueue />));
    const textarea = await screen.findByDisplayValue("RoboCo v0.19.0 is here!");
    fireEvent.change(textarea, { target: { value: "x".repeat(281) } });

    expect(screen.getByText("281/280")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeDisabled();
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
