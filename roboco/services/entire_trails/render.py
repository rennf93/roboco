"""What the trails bridge writes to Entire, rendered from RoboCo's rows.

Pure functions. A finding's fields are rendered once, when its outbox row is
written, and stored with the row: Entire keys a finding retry on its client
id and refuses the same id with different fields (``client_id_conflict``),
so a retry must send byte-identical text. A trail's text is rendered at
drain time from the current rows: a trail update has no such key, and the
latest state is the one worth showing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from roboco.db.tables import TaskReviewFindingTable, TaskTable

# RoboCo's four finding severities onto Entire's three.
_SEVERITY = {"blocker": "high", "major": "high", "minor": "medium", "nit": "low"}
# Task priority 0 (P0, highest) .. 3 (P3, lowest).
_PRIORITY = {0: "urgent", 1: "high", 2: "medium", 3: "low"}
_TRAIL_BODY_LIMIT = 60_000


def short_id(task_id: object) -> str:
    return str(task_id)[:8]


def entire_severity(severity: str) -> str:
    return _SEVERITY.get(severity, "medium")


def trail_title(root: TaskTable) -> str:
    return f"[{short_id(root.id)}] {root.title}"


def trail_type(root: TaskTable) -> str:
    return "feature" if str(root.task_type) == "code" else "task"


def trail_priority(root: TaskTable) -> str:
    return _PRIORITY.get(int(root.priority), "medium")


@dataclass(frozen=True)
class TreeRow:
    """One task under the trail's root, for the trail body's task table."""

    task: TaskTable
    depth: int


def _risk_line(risk: object) -> str:
    if not isinstance(risk, dict):
        return str(risk).strip()
    mitigation = risk.get("mitigation")
    text = str(risk.get("description", "")).strip()
    return f"{text} (mitigation: {mitigation})" if mitigation else text


def _question_line(question: object) -> str:
    if not isinstance(question, dict):
        return str(question).strip()
    answer = question.get("answer")
    text = str(question.get("question", "")).strip()
    return f"{text} (answer: {answer})" if answer else text


def _plan_steps(steps: Sequence[object]) -> list[str]:
    lines = []
    for step in steps:
        if isinstance(step, dict):
            done = "x" if step.get("completed") else " "
            text = step.get("title") or step.get("description") or ""
        else:
            done, text = " ", step
        lines.append(f"- [{done}] {str(text).strip()}")
    return [*lines, ""] if lines else []


def _plan_section(plan: dict[str, Any] | str | None) -> list[str]:
    """The root's plan: ``TaskService.set_plan`` stores either the
    panel-shaped dict (approach, sub_tasks, risks, open_questions) or a
    plain string."""
    if not plan:
        return []
    if not isinstance(plan, dict):
        return ["## Plan", "", str(plan).strip(), ""]
    lines = ["## Plan", ""]
    approach = plan.get("approach") or plan.get("text")
    if approach:
        lines += [str(approach).strip(), ""]
    lines += _plan_steps(plan.get("sub_tasks") or [])
    for key, heading, render in (
        ("risks", "Risks", _risk_line),
        ("open_questions", "Open questions", _question_line),
    ):
        items = plan.get(key) or []
        if items:
            lines += [f"**{heading}**", ""]
            lines += [f"- {render(item)}" for item in items]
            lines.append("")
    return lines


def _tasks_section(rows: Sequence[TreeRow]) -> list[str]:
    if not rows:
        return []
    lines = [
        "## Tasks",
        "",
        "| Task | Team | Status | Branch | PR |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        task = row.task
        indent = "&nbsp;&nbsp;" * row.depth
        title = str(task.title).replace("|", "\\|")
        branch = f"`{task.branch_name}`" if task.branch_name else ""
        pr = (
            f"[#{task.pr_number}]({task.pr_url})"
            if task.pr_number and task.pr_url
            else ""
        )
        lines.append(
            f"| {indent}[{short_id(task.id)}] {title} | {task.team} | "
            f"{task.status} | {branch} | {pr} |"
        )
    lines.append("")
    return lines


def trail_body(
    root: TaskTable, rows: Sequence[TreeRow], *, closing_note: str | None = None
) -> str:
    """The trail's description: the root's intent, plan and live task table.
    RoboCo stays canonical; the footer says so."""
    lines = [str(root.description).strip(), ""]
    criteria = list(root.acceptance_criteria or [])
    if criteria:
        lines += ["## Acceptance criteria", ""]
        lines += [f"- {str(c).strip()}" for c in criteria]
        lines.append("")
    lines += _plan_section(root.plan)
    lines += _tasks_section(rows)
    if closing_note:
        lines += ["## Outcome", "", closing_note, ""]
    lines.append(
        f"_Mirrored from RoboCo task `{root.id}` ({root.status}). RoboCo is "
        "canonical: change the task there, not here._"
    )
    body = "\n".join(lines)
    if len(body) > _TRAIL_BODY_LIMIT:
        body = body[:_TRAIL_BODY_LIMIT] + "\n\n_(truncated)_"
    return body


def trail_fields(
    root: TaskTable, rows: Sequence[TreeRow], *, closing_note: str | None = None
) -> dict[str, str]:
    """A trail's title, body, type and priority, as ``trail create`` and
    ``trail update`` take them."""
    return {
        "title": trail_title(root),
        "body": trail_body(root, rows, closing_note=closing_note),
        "type": trail_type(root),
        "priority": trail_priority(root),
    }


def finding_body(finding: TaskReviewFindingTable, task: TaskTable) -> str:
    """A ledger row as an Entire finding's text. Stable for a given row: the
    same row always renders the same text (see the module docstring)."""
    head = finding.criterion or "Review finding"
    lines = [
        f"**{head}** ({finding.severity}, {finding.origin} review, "
        f"round {finding.round})",
        "",
        f"Expected: {finding.expected}",
        f"Actual: {finding.actual}",
    ]
    if finding.fix:
        lines.append(f"Fix: {finding.fix}")
    if finding.evidence:
        lines += ["", "Evidence:", "", finding.evidence]
    author = f" by {finding.author_slug}" if finding.author_slug else ""
    lines += [
        "",
        f"RoboCo finding `{finding.id}`{author} on task "
        f"[{short_id(task.id)}] {task.title}",
    ]
    return "\n".join(lines)


def addressed_finding_body(finding: TaskReviewFindingTable, task: TaskTable) -> str:
    """The finding's text once its author says it is fixed (still open on
    Entire until a reviewer verifies it)."""
    commit = finding.addressed_by_commit or "an unrecorded commit"
    note = f": {finding.resolution_note}" if finding.resolution_note else ""
    return f"{finding_body(finding, task)}\n\nAddressed in {commit}{note}"
