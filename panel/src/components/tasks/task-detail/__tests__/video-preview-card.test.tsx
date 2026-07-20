import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";
import type { VideoPreviewFrames } from "@/lib/api/video";

const { getPreviewFrames, getPreviewFrameBlob } = vi.hoisted(() => ({
  getPreviewFrames: vi.fn<() => Promise<VideoPreviewFrames>>(),
  getPreviewFrameBlob: vi.fn(
    async () => new Blob(["fake-png-bytes"], { type: "image/png" }),
  ),
}));

vi.mock("@/lib/api", () => ({
  videoApi: { getPreviewFrames, getPreviewFrameBlob },
}));

import { VideoPreviewCard } from "../video-preview-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-1",
    title: "Video: launch teaser",
    description: "d",
    status: TaskStatus.AWAITING_CEO_APPROVAL,
    team: Team.UX_UI,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    source: "video",
    ...overrides,
  } as unknown as Task;
}

const FRAMES: VideoPreviewFrames = {
  task_id: "task-1",
  composition_id: "Intro",
  duration_seconds: 6.4,
  head_sha: "abc1234",
  dirty: false,
  rendered_at: "2026-07-19T12:00:00Z",
  frames: {
    vertical: [
      { index: 1, file: "frame-01-of-2-at-1.5s.png", timestamp_seconds: 1.5 },
      { index: 2, file: "frame-02-of-2-at-4.5s.png", timestamp_seconds: 4.5 },
    ],
    square: [
      { index: 1, file: "frame-01-of-1-at-3.0s.png", timestamp_seconds: 3.0 },
    ],
  },
};

describe("VideoPreviewCard", () => {
  beforeEach(() => {
    getPreviewFrames.mockReset();
    getPreviewFrameBlob.mockClear();
    let objectUrlCount = 0;
    globalThis.URL.createObjectURL = vi.fn(
      () => `blob:mock-url-${++objectUrlCount}`,
    );
    globalThis.URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders composition metadata and blob-fetches the first frame of the default cut", async () => {
    getPreviewFrames.mockResolvedValueOnce(FRAMES);
    render(withQueryClient(<VideoPreviewCard task={buildTask()} />));

    expect(await screen.findByText("Intro")).toBeInTheDocument();
    expect(screen.getByText("6.4s clip")).toBeInTheDocument();
    await waitFor(() =>
      expect(getPreviewFrameBlob).toHaveBeenCalledWith(
        "task-1",
        "vertical",
        "frame-01-of-2-at-1.5s.png",
      ),
    );
    expect(screen.getByText(/Frame 1\/2/)).toBeInTheDocument();
    expect(screen.getByText(/1\.5s into the clip/)).toBeInTheDocument();
  });

  it("steps to the next frame within a cut", async () => {
    getPreviewFrames.mockResolvedValueOnce(FRAMES);
    render(withQueryClient(<VideoPreviewCard task={buildTask()} />));
    await screen.findByText("Intro");
    await waitFor(() => expect(getPreviewFrameBlob).toHaveBeenCalledTimes(1));

    expect(screen.getByLabelText("Previous frame")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Next frame"));

    await waitFor(() =>
      expect(getPreviewFrameBlob).toHaveBeenCalledWith(
        "task-1",
        "vertical",
        "frame-02-of-2-at-4.5s.png",
      ),
    );
    expect(screen.getByText(/Frame 2\/2/)).toBeInTheDocument();
    expect(screen.getByText(/4\.5s into the clip/)).toBeInTheDocument();
    expect(screen.getByLabelText("Next frame")).toBeDisabled();
  });

  it("switches cuts via the 9:16/1:1 toggle and re-fetches that orientation's first frame", async () => {
    getPreviewFrames.mockResolvedValueOnce(FRAMES);
    render(withQueryClient(<VideoPreviewCard task={buildTask()} />));
    await screen.findByText("Intro");
    await waitFor(() =>
      expect(getPreviewFrameBlob).toHaveBeenCalledWith(
        "task-1",
        "vertical",
        "frame-01-of-2-at-1.5s.png",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "1:1" }));
    await waitFor(() =>
      expect(getPreviewFrameBlob).toHaveBeenCalledWith(
        "task-1",
        "square",
        "frame-01-of-1-at-3.0s.png",
      ),
    );
    expect(screen.getByText(/Frame 1\/1/)).toBeInTheDocument();
  });

  it("flags an orientation with no rendered frames as missing and disables its toggle", async () => {
    getPreviewFrames.mockResolvedValueOnce({
      ...FRAMES,
      frames: { vertical: FRAMES.frames.vertical },
    });
    render(withQueryClient(<VideoPreviewCard task={buildTask()} />));
    await screen.findByText("Intro");

    const squareButton = screen.getByRole("button", { name: /1:1/ });
    expect(squareButton).toHaveTextContent("(missing)");
    expect(squareButton).toBeDisabled();
  });

  it("shows an uncommitted-changes badge when the render was dirty", async () => {
    getPreviewFrames.mockResolvedValueOnce({ ...FRAMES, dirty: true });
    render(withQueryClient(<VideoPreviewCard task={buildTask()} />));
    expect(await screen.findByText("uncommitted changes")).toBeInTheDocument();
  });

  it("shows a muted empty state when nothing has been rendered yet (404)", async () => {
    getPreviewFrames.mockRejectedValueOnce(new Error("not found"));
    render(withQueryClient(<VideoPreviewCard task={buildTask()} />));
    expect(
      await screen.findByText(/No render preview yet/),
    ).toBeInTheDocument();
    expect(getPreviewFrameBlob).not.toHaveBeenCalled();
  });
});
