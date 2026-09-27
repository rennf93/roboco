import { describe, it, expect } from "vitest";
import { TaskStatus, type Task } from "@/types";
import { deriveStuckState, formatActiveTime } from "../use-stuck-state";

// Minimal task stub — the derivation only reads the wedge-contract fields
// plus status/blocker_resolver_type.
function baseTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-1",
    title: "Task",
    description: "",
    acceptance_criteria: [],
    status: TaskStatus.IN_PROGRESS,
    priority: 2,
    sequence: 0,
    team: "frontend" as Task["team"],
    created_by: "ceo",
    assigned_to: null,
    parent_task_id: null,
    dependency_ids: [],
    blocker_ids: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    claimed_at: null,
    started_at: null,
    completed_at: null,
    target_date: null,
    estimated_complexity: "medium" as Task["estimated_complexity"],
    nature: "technical" as Task["nature"],
    task_type: "code" as Task["task_type"],
    project_id: null,
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    plan: null,
    checkpoints: [],
    progress_updates: [],
    commits: [],
    dev_notes: null,
    qa_notes: null,
    auditor_notes: null,
    quick_context: null,
    self_verified: false,
    qa_verified: null,
    branch_name: null,
    pr_number: null,
    pr_url: null,
    ...overrides,
  };
}

describe("deriveStuckState", () => {
  it("is not stuck on a healthy task", () => {
    const state = deriveStuckState(baseTask());
    expect(state.isStuck).toBe(false);
    expect(state.reasons).toEqual([]);
  });

  it("is stuck when open wedge strikes are present", () => {
    const state = deriveStuckState(
      baseTask({ open_wedge_strikes: 2, wedge_cycle: [] }),
    );
    expect(state.isStuck).toBe(true);
    expect(state.reasons).toEqual(["2 open wedge strikes"]);
  });

  it("is stuck when blocked for human resolution", () => {
    const state = deriveStuckState(
      baseTask({
        status: TaskStatus.BLOCKED,
        blocker_resolver_type: "human",
      }),
    );
    expect(state.isStuck).toBe(true);
    expect(state.reasons).toEqual(["blocked for human resolution"]);
  });

  it("is not stuck when blocked for an agent", () => {
    const state = deriveStuckState(
      baseTask({
        status: TaskStatus.BLOCKED,
        blocker_resolver_type: "agent",
      }),
    );
    expect(state.isStuck).toBe(false);
  });

  it("returns the wedge cycle entries verbatim", () => {
    const cycle = [{ actor: "main-pm", verb: "respawn", timestamp: "2026-09-27T00:00:00Z" }];
    const state = deriveStuckState(
      baseTask({ open_wedge_strikes: 1, wedge_cycle: cycle }),
    );
    expect(state.wedgeCycle).toEqual(cycle);
  });

  it("handles a null/undefined task", () => {
    expect(deriveStuckState(null).isStuck).toBe(false);
    expect(deriveStuckState(undefined).isStuck).toBe(false);
  });
});

describe("formatActiveTime", () => {
  it("passes pre-formatted strings through", () => {
    expect(formatActiveTime("2 days")).toBe("2 days");
  });

  it("formats seconds into a coarse clock", () => {
    expect(formatActiveTime(3600 * 26 + 60 * 5)).toBe("1d 2h");
    expect(formatActiveTime(60 * 14)).toBe("14m");
    expect(formatActiveTime(30)).toBe("30s");
  });

  it("returns null for empty values", () => {
    expect(formatActiveTime(null)).toBeNull();
    expect(formatActiveTime(undefined)).toBeNull();
    expect(formatActiveTime("")).toBeNull();
  });
});
