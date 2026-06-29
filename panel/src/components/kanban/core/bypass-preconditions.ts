import { TaskStatus, type Task } from "@/types";

// A drag on the operator kanban routes the status move through the admin
// status-override, which bypasses the in-band lifecycle validator entirely.
// That override is intentional — it's how an operator recovers a task wedged
// in a state with no valid in-band move. But it also lets a careless drag skip
// material preconditions silently (completing a task with no PR, QA-bypassing
// a task with no PR, finishing docs on a task whose docs aren't complete).
//
// This returns the human-readable preconditions the dragged move would skip,
// computed only from what the panel can see reliably on the task (and its
// in-list children). Precision over recall: we never claim a precondition is
// satisfied when we can't verify it, and we never false-alarm on a transition
// that gates on nothing we can check. An empty list means the drag skips no
// material precondition the panel can detect — proceed without a prompt.

// Targets whose in-band entry requires an open PR.
const PR_REQUIRED: ReadonlySet<TaskStatus> = new Set([
  TaskStatus.AWAITING_QA,
  TaskStatus.AWAITING_DOCUMENTATION,
  TaskStatus.NEEDS_REVISION,
  TaskStatus.AWAITING_PR_REVIEW,
  TaskStatus.AWAITING_CEO_APPROVAL,
  TaskStatus.COMPLETED,
]);

// Targets whose in-band entry requires the documentation phase to be complete.
const DOCS_REQUIRED: ReadonlySet<TaskStatus> = new Set([
  TaskStatus.AWAITING_PM_REVIEW,
  TaskStatus.COMPLETED,
]);

// Coordination-root targets: the in-band entry requires all subtasks terminal.
const SUBTASKS_TERMINAL_REQUIRED: ReadonlySet<TaskStatus> = new Set([
  TaskStatus.AWAITING_PM_REVIEW,
  TaskStatus.AWAITING_CEO_APPROVAL,
  TaskStatus.COMPLETED,
]);

const TERMINAL: ReadonlySet<TaskStatus> = new Set([
  TaskStatus.COMPLETED,
  TaskStatus.CANCELLED,
]);

export function skippedPreconditions(
  task: Task,
  newStatus: TaskStatus,
  allTasks: Task[],
): string[] {
  const skipped: string[] = [];

  if (PR_REQUIRED.has(newStatus) && task.pr_number == null) {
    skipped.push("no open PR");
  }

  if (DOCS_REQUIRED.has(newStatus) && !task.docs_complete) {
    skipped.push("documentation not marked complete");
  }

  // The submit-qa gate (verifying -> awaiting_qa) additionally requires
  // self-verification, at least one linked commit, and a progress update.
  if (newStatus === TaskStatus.AWAITING_QA) {
    if (!task.self_verified) skipped.push("not self-verified");
    if (task.commits.length === 0) skipped.push("no commits linked");
    if (task.progress_updates.length === 0) skipped.push("no progress updates");
  }

  // Coordination-root targets require every subtask in a terminal state. The
  // board's task list is team-filtered, so a root's children may live in other
  // teams and be absent here — only warn on children we can actually see that
  // are not terminal; never fabricate a "subtasks terminal" claim we can't
  // verify.
  if (SUBTASKS_TERMINAL_REQUIRED.has(newStatus)) {
    const children = allTasks.filter((t) => t.parent_task_id === task.id);
    const nonTerminal = children.filter((c) => !TERMINAL.has(c.status));
    if (nonTerminal.length > 0) {
      skipped.push(`${nonTerminal.length} non-terminal subtask(s)`);
    }
  }

  return skipped;
}
