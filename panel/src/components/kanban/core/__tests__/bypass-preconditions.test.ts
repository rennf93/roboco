import { describe, it, expect } from "vitest";
import { TaskStatus, Team, TaskType, type Task } from "@/types";
import { skippedPreconditions } from "../bypass-preconditions";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "A task",
    description: "desc",
    acceptance_criteria: ["a"],
    status: TaskStatus.IN_PROGRESS,
    priority: 2,
    sequence: 0,
    team: Team.BACKEND,
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
    estimated_complexity: "M" as never,
    nature: "feature" as never,
    task_type: TaskType.CODE,
    project_id: "p1",
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    plan: null,
    checkpoints: [],
    progress_updates: [],
    commits: [],
    self_verified: false,
    qa_verified: null,
    sessions: [],
    branch_name: null,
    pr_number: null,
    pr_url: null,
    ...overrides,
  } as unknown as Task;
}

describe("skippedPreconditions — F020 (what an admin-override drag skips)", () => {
  it("flags a PR-less drag to awaiting_qa with the submit-qa gate's missing checks", () => {
    const task = buildTask({ pr_number: null, self_verified: false });
    const skipped = skippedPreconditions(task, TaskStatus.AWAITING_QA, [task]);
    expect(skipped).toEqual([
      "no open PR",
      "not self-verified",
      "no commits linked",
      "no progress updates",
    ]);
  });

  it("stays silent when a task meets the awaiting_qa preconditions (no false alarm)", () => {
    const task = buildTask({
      pr_number: 42,
      self_verified: true,
      commits: [{ sha: "abc", message: "m", timestamp: "t" } as never],
      progress_updates: [{ note: "p" } as never],
    });
    expect(skippedPreconditions(task, TaskStatus.AWAITING_QA, [task])).toEqual(
      [],
    );
  });

  it("flags a PR-less, undocumented drag to completed", () => {
    const task = buildTask({ pr_number: null, docs_complete: false });
    const skipped = skippedPreconditions(task, TaskStatus.COMPLETED, [task]);
    expect(skipped).toContain("no open PR");
    expect(skipped).toContain("documentation not marked complete");
  });

  it("flags non-terminal subtasks when completing a coordination root", () => {
    const parent = buildTask({ id: "root", pr_number: 7, docs_complete: true });
    const children = [
      buildTask({
        id: "c1",
        parent_task_id: "root",
        status: TaskStatus.COMPLETED,
      }),
      buildTask({
        id: "c2",
        parent_task_id: "root",
        status: TaskStatus.IN_PROGRESS,
      }),
    ];
    const skipped = skippedPreconditions(parent, TaskStatus.COMPLETED, [
      parent,
      ...children,
    ]);
    expect(skipped).toContain("1 non-terminal subtask(s)");
  });

  it("does not fabricate a subtask warning when no children are visible", () => {
    // The board's task list is team-filtered; a root's children may be in other
    // teams and absent here. Precision over recall: don't claim "subtasks
    // terminal" we can't verify — and don't false-alarm either.
    const parent = buildTask({ id: "root", pr_number: 7, docs_complete: true });
    expect(
      skippedPreconditions(parent, TaskStatus.COMPLETED, [parent]),
    ).toEqual([]);
  });

  it("stays silent for benign transitions that skip no material precondition", () => {
    // pending -> claimed, in_progress -> blocked, etc. don't gate on PR/docs.
    const task = buildTask({ status: TaskStatus.PENDING });
    expect(skippedPreconditions(task, TaskStatus.CLAIMED, [task])).toEqual([]);
    const inProgress = buildTask({ status: TaskStatus.IN_PROGRESS });
    expect(
      skippedPreconditions(inProgress, TaskStatus.BLOCKED, [inProgress]),
    ).toEqual([]);
  });

  it("flags a PR-less drag to awaiting_documentation (pass-qa gate needs a PR)", () => {
    const task = buildTask({ pr_number: null });
    expect(
      skippedPreconditions(task, TaskStatus.AWAITING_DOCUMENTATION, [task]),
    ).toContain("no open PR");
  });

  it("flags missing docs for a drag to awaiting_pm_review", () => {
    const task = buildTask({ pr_number: 9, docs_complete: false });
    const skipped = skippedPreconditions(task, TaskStatus.AWAITING_PM_REVIEW, [
      task,
    ]);
    expect(skipped).toEqual(["documentation not marked complete"]);
  });
});
