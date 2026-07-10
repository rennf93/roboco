import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { VideoPipelineItem, VideoPost } from "@/lib/api/video";

const { resolveApproveRef } = vi.hoisted(() => ({
  resolveApproveRef: { current: null as null | ((v: unknown) => void) },
}));

const {
  listPosts,
  listPipeline,
  approve,
  reject,
  requestVideo,
  getMediaBlob,
  rerender,
} = vi.hoisted(() => ({
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
          mp4_paths: {
            vertical: "/fake/vertical.mp4",
            square: "/fake/square.mp4",
          },
        },
      ] as VideoPost[],
  ),
  listPipeline: vi.fn(async (): Promise<VideoPipelineItem[]> => []),
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
  rerender: vi.fn(async () => undefined),
}));

vi.mock("@/lib/api", () => ({
  videoApi: {
    listPosts,
    listPipeline,
    approve,
    reject,
    requestVideo,
    getMediaBlob,
    rerender,
  },
}));
// ProjectSelector: a button that sets the project, mirroring
// create-task-dialog.test.tsx — bypasses the data-fetching combobox.
vi.mock("@/components/projects/project-selector", () => ({
  ProjectSelector: ({ onChange }: { onChange: (v: string | null) => void }) => (
    <button type="button" onClick={() => onChange("p-1")}>
      Set Project
    </button>
  ),
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
    listPipeline.mockClear();
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

  it("shows the fetched title and script instead of dropping them", async () => {
    render(withQueryClient(<VideoPostQueue />));
    expect(
      await screen.findByText("Video: release v0.19.0"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("RoboCo v0.19.0 just shipped!"),
    ).toBeInTheDocument();
  });

  it("flags a missing cut as disabled instead of silently blanking the player", async () => {
    listPosts.mockResolvedValueOnce([
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
        mp4_paths: { vertical: "/fake/vertical.mp4" }, // square never rendered
      },
    ] as VideoPost[]);
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");

    const squareButton = screen.getByRole("button", { name: /1:1/ });
    expect(squareButton).toHaveTextContent("(missing)");
    expect(squareButton).toBeDisabled();
    // The present cut is unaffected — no "(missing)" suffix, not disabled.
    const verticalButton = screen.getByRole("button", { name: /9:16/ });
    expect(verticalButton).not.toHaveTextContent("(missing)");
    expect(verticalButton).not.toBeDisabled();
    await waitFor(() =>
      expect(getMediaBlob).toHaveBeenCalledWith("v-1", "vertical"),
    );
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

  it("requests an on-demand video with the chosen project, occasion, brief, and platforms", async () => {
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");

    fireEvent.click(screen.getByRole("button", { name: /Request a video/ }));
    fireEvent.click(screen.getByRole("button", { name: "Set Project" }));
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
        project_id: "p-1",
      }),
    );
  });

  it("keeps Request disabled until a project is picked", async () => {
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");

    fireEvent.click(screen.getByRole("button", { name: /Request a video/ }));
    fireEvent.change(screen.getByLabelText("Occasion"), {
      target: { value: "Founder's Day" },
    });
    fireEvent.change(screen.getByLabelText("Brief"), {
      target: { value: "Celebrate the founding." },
    });
    expect(screen.getByRole("button", { name: "Request" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Set Project" }));
    expect(screen.getByRole("button", { name: "Request" })).not.toBeDisabled();
  });

  it("shows the keys/engine empty copy when nothing is in the pipeline either", async () => {
    listPosts.mockResolvedValueOnce([]);
    listPipeline.mockResolvedValueOnce([]);
    render(withQueryClient(<VideoPostQueue />));
    expect(await screen.findByText(/No drafts yet/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Request a video/ }),
    ).toBeInTheDocument();
  });

  it("shows an in-flight count instead of the keys/engine copy when the pipeline is non-empty", async () => {
    listPosts.mockResolvedValueOnce([]);
    listPipeline.mockResolvedValueOnce([
      {
        task_id: "vp-1",
        title: "Video: launch teaser",
        occasion: "launch",
        status: "in_progress",
        pr_number: null,
        composition_id: null,
        render_status: null,
        render_attempts: 0,
        max_attempts: 5,
        render_error: null,
      },
    ] as VideoPipelineItem[]);
    render(withQueryClient(<VideoPostQueue />));
    expect(
      await screen.findByText(/1 video in flight — nothing rendered yet/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No drafts yet/)).not.toBeInTheDocument();
  });

  it("does not show a re-render button when the draft's render is healthy", async () => {
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");
    expect(
      screen.queryByRole("button", { name: /Re-render/ }),
    ).not.toBeInTheDocument();
  });

  it("shows a re-render button only on a stale draft and triggers the backend re-render action", async () => {
    listPosts.mockResolvedValueOnce([
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
        mp4_paths: { vertical: "/fake/vertical.mp4" },
        source_task_id: "auth-1",
        render_status: "failed",
      },
    ] as VideoPost[]);
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");

    const rerenderButton = screen.getByRole("button", { name: /Re-render/ });
    fireEvent.click(rerenderButton);

    await waitFor(() => expect(rerender).toHaveBeenCalledWith("auth-1"));
  });

  it("shows the live composition preview iframe with captions when composition_id is present", async () => {
    listPosts.mockResolvedValueOnce([
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
        mp4_paths: { vertical: "/fake/vertical.mp4" },
        source_task_id: "auth-1",
        composition_id: "release-recap",
      },
    ] as VideoPost[]);
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");

    const iframe = document.querySelector("iframe");
    expect(iframe).toBeInTheDocument();
    expect(iframe?.getAttribute("src")).toContain(
      "/video/preview/auth-1/motion/compositions/release-recap/vertical.html",
    );
    expect(screen.getByText("Captions as they will post")).toBeInTheDocument();
  });

  it("hides the composition preview panel when the draft carries no composition_id", async () => {
    render(withQueryClient(<VideoPostQueue />));
    await screen.findByText("release");
    expect(document.querySelector("iframe")).not.toBeInTheDocument();
  });
});
