import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { VideoPipelineItem } from "@/lib/api/video";

const { listPipeline, rerender } = vi.hoisted(() => ({
  listPipeline: vi.fn(async (): Promise<VideoPipelineItem[]> => []),
  rerender: vi.fn(async () => undefined),
}));

vi.mock("@/lib/api", () => ({
  videoApi: { listPipeline, rerender },
}));

import { VideoPipelineStrip } from "../video-pipeline-strip";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

const AUTHORING: VideoPipelineItem = {
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
};

const AWAITING_APPROVAL: VideoPipelineItem = {
  ...AUTHORING,
  task_id: "vp-2",
  status: "awaiting_ceo_approval",
};

const RENDERING: VideoPipelineItem = {
  ...AUTHORING,
  task_id: "vp-3",
  status: "completed",
  composition_id: "Intro",
  render_attempts: 2,
};

const FAILED: VideoPipelineItem = {
  ...AUTHORING,
  task_id: "vp-4",
  status: "completed",
  composition_id: "Intro",
  render_status: "failed",
  render_attempts: 5,
  render_error: "sidecar timeout",
};

describe("VideoPipelineStrip", () => {
  beforeEach(() => {
    listPipeline.mockClear();
    rerender.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when the pipeline is empty", async () => {
    render(withQueryClient(<VideoPipelineStrip />));
    await waitFor(() => expect(listPipeline).toHaveBeenCalled());
    expect(screen.queryByText("Video Pipeline")).not.toBeInTheDocument();
  });

  it("shows a stage chip per in-flight item, including the failure reason", async () => {
    listPipeline.mockResolvedValueOnce([
      AUTHORING,
      AWAITING_APPROVAL,
      RENDERING,
      FAILED,
    ]);
    render(withQueryClient(<VideoPipelineStrip />));

    expect(await screen.findByText("Video Pipeline")).toBeInTheDocument();
    expect(screen.getByText("Authoring")).toBeInTheDocument();
    expect(screen.getByText("Awaiting your approval")).toBeInTheDocument();
    expect(screen.getByText("Rendering (attempt 2/5)")).toBeInTheDocument();
    expect(
      screen.getByText("Render failed: sidecar timeout"),
    ).toBeInTheDocument();
  });

  it("deep-links only the awaiting-approval row to the task", async () => {
    listPipeline.mockResolvedValueOnce([AUTHORING, AWAITING_APPROVAL]);
    render(withQueryClient(<VideoPipelineStrip />));
    await screen.findByText("Video Pipeline");

    const reviewLinks = screen.getAllByRole("link", { name: "Review" });
    expect(reviewLinks).toHaveLength(1);
    expect(reviewLinks[0]).toHaveAttribute("href", "/tasks/vp-2");
  });

  it("shows a re-render button on rendering and render_failed rows with a composition, but not on authoring/awaiting-approval rows", async () => {
    listPipeline.mockResolvedValueOnce([
      AUTHORING,
      AWAITING_APPROVAL,
      RENDERING,
      FAILED,
    ]);
    render(withQueryClient(<VideoPipelineStrip />));
    await screen.findByText("Video Pipeline");

    const rerenderButtons = screen.getAllByRole("button", {
      name: /Re-render/,
    });
    expect(rerenderButtons).toHaveLength(2);
  });

  it("triggers the backend re-render action for the clicked row's authoring task id, behind a confirm dialog", async () => {
    listPipeline.mockResolvedValueOnce([FAILED]);
    render(withQueryClient(<VideoPipelineStrip />));
    await screen.findByText("Video Pipeline");

    fireEvent.click(screen.getByRole("button", { name: /Re-render/ }));
    expect(rerender).not.toHaveBeenCalled();
    const confirmButton = await screen.findByRole("button", {
      name: /Re-render/,
    });
    fireEvent.click(confirmButton);

    await waitFor(() => expect(rerender).toHaveBeenCalledWith("vp-4"));
  });
});
