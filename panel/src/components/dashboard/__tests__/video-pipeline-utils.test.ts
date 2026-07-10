import { describe, it, expect } from "vitest";
import {
  derivePipelineStage,
  pipelineStageLabel,
  pipelineStageColor,
} from "../video-pipeline-utils";

const BASE = {
  status: "in_progress",
  render_status: null,
  render_attempts: 0,
  max_attempts: 5,
  render_error: null,
};

describe("derivePipelineStage", () => {
  it.each([
    "backlog",
    "pending",
    "claimed",
    "in_progress",
    "blocked",
    "paused",
    "verifying",
    "needs_revision",
  ])("maps %s to authoring", (status) => {
    expect(derivePipelineStage({ ...BASE, status })).toEqual({
      kind: "authoring",
    });
  });

  it.each([
    "awaiting_qa",
    "awaiting_documentation",
    "awaiting_pr_review",
    "awaiting_pm_review",
  ])("maps %s to in_review", (status) => {
    expect(derivePipelineStage({ ...BASE, status })).toEqual({
      kind: "in_review",
    });
  });

  it("maps awaiting_ceo_approval to awaiting_approval", () => {
    expect(
      derivePipelineStage({ ...BASE, status: "awaiting_ceo_approval" }),
    ).toEqual({ kind: "awaiting_approval" });
  });

  it("maps a completed task with no render_status to rendering, carrying attempts", () => {
    expect(
      derivePipelineStage({
        ...BASE,
        status: "completed",
        render_status: null,
        render_attempts: 2,
        max_attempts: 5,
      }),
    ).toEqual({ kind: "rendering", attempt: 2, maxAttempts: 5 });
  });

  it("maps a completed task with render_status='failed' to render_failed, carrying the reason", () => {
    expect(
      derivePipelineStage({
        ...BASE,
        status: "completed",
        render_status: "failed",
        render_error: "sidecar timeout",
      }),
    ).toEqual({ kind: "render_failed", reason: "sidecar timeout" });
  });

  it("render_failed carries a null reason when the marker holds none", () => {
    expect(
      derivePipelineStage({
        ...BASE,
        status: "completed",
        render_status: "failed",
        render_error: null,
      }),
    ).toEqual({ kind: "render_failed", reason: null });
  });
});

describe("pipelineStageLabel", () => {
  it("labels every stage kind", () => {
    expect(pipelineStageLabel({ kind: "authoring" })).toBe("Authoring");
    expect(pipelineStageLabel({ kind: "in_review" })).toBe("In review");
    expect(pipelineStageLabel({ kind: "awaiting_approval" })).toBe(
      "Awaiting your approval",
    );
    expect(
      pipelineStageLabel({ kind: "rendering", attempt: 2, maxAttempts: 5 }),
    ).toBe("Rendering (attempt 2/5)");
    expect(pipelineStageLabel({ kind: "render_failed", reason: "boom" })).toBe(
      "Render failed: boom",
    );
    expect(pipelineStageLabel({ kind: "render_failed", reason: null })).toBe(
      "Render failed",
    );
  });
});

describe("pipelineStageColor", () => {
  it("returns a distinct class per stage kind", () => {
    const stages: Parameters<typeof pipelineStageColor>[0][] = [
      { kind: "authoring" },
      { kind: "in_review" },
      { kind: "awaiting_approval" },
      { kind: "rendering", attempt: 1, maxAttempts: 5 },
      { kind: "render_failed", reason: null },
    ];
    const colors = stages.map(pipelineStageColor);
    expect(new Set(colors).size).toBe(colors.length);
  });
});
