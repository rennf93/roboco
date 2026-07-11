/**
 * Pure stage derivation for the video pipeline strip (extracted for direct
 * unit testing). A VideoPipelineItem's `status` covers the pre-render
 * delivery lifecycle; once it reaches COMPLETED, `render_status` /
 * `render_attempts` (from the orchestration_markers.video_draft JSON) take
 * over to describe the render loop's own retry/failure states.
 */

import type { VideoPipelineItem } from "@/lib/api/video";

export type PipelineStage =
  | { kind: "authoring" }
  | { kind: "in_review" }
  | { kind: "awaiting_approval" }
  | { kind: "rendering"; attempt: number; maxAttempts: number }
  | { kind: "render_failed"; reason: string | null };

// Statuses between "self-verified" and "PM merges" — the dev's work is done,
// a reviewer/PM is looking at it.
const IN_REVIEW_STATUSES = new Set([
  "awaiting_qa",
  "awaiting_documentation",
  "awaiting_pr_review",
  "awaiting_pm_review",
]);

/**
 * Derives the pipeline strip's stage chip from an item's raw fields. Only
 * reads the subset of VideoPipelineItem the derivation needs, so callers
 * (and tests) can pass a partial fixture.
 */
export function derivePipelineStage(
  item: Pick<
    VideoPipelineItem,
    | "status"
    | "render_status"
    | "render_attempts"
    | "max_attempts"
    | "render_error"
  >,
): PipelineStage {
  if (item.status === "completed") {
    if (item.render_status === "failed") {
      return { kind: "render_failed", reason: item.render_error };
    }
    // render_status unset (or any non-terminal value) — still retrying.
    return {
      kind: "rendering",
      attempt: item.render_attempts,
      maxAttempts: item.max_attempts,
    };
  }
  if (item.status === "awaiting_ceo_approval")
    return { kind: "awaiting_approval" };
  if (IN_REVIEW_STATUSES.has(item.status)) return { kind: "in_review" };
  return { kind: "authoring" };
}

/** Human-readable label for a stage — the chip's display text. */
export function pipelineStageLabel(stage: PipelineStage): string {
  switch (stage.kind) {
    case "authoring":
      return "Authoring";
    case "in_review":
      return "In review";
    case "awaiting_approval":
      return "Awaiting your approval";
    case "rendering":
      return `Rendering (attempt ${stage.attempt}/${stage.maxAttempts})`;
    case "render_failed":
      return stage.reason ? `Render failed: ${stage.reason}` : "Render failed";
  }
}

/** Tailwind bg class for the stage badge — mirrors task-status-badge.tsx. */
export function pipelineStageColor(stage: PipelineStage): string {
  switch (stage.kind) {
    case "authoring":
      return "bg-blue-600";
    case "in_review":
      return "bg-teal-600";
    case "awaiting_approval":
      return "bg-amber-600";
    case "rendering":
      return "bg-purple-500";
    case "render_failed":
      return "bg-red-600";
  }
}
