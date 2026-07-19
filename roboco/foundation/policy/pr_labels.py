"""PR-label derivation — pure predicates from task/PR shape to GitHub labels.

The org-structure label vocabulary every fleet PR carries so a human can triage
the queue at a glance: which branch a PR targets (``to {base_branch}``, the
PR's REAL base — e.g. ``to master``, ``to slave``, or any project-specific
env-ladder rung), whether it is an assembled root PR (``root``), a MegaTask
member (``MegaTask``), and which layer owns it (``main-pm`` / ``cell/{team}`` /
``subtask/{team}``).

Pure + DB-free so it is unit-testable; the git service's best-effort
``_apply_pr_labels`` helper posts the result to the GitHub labels API. Inputs are
typed ``object | None`` because callers pass ORM enum members or ``.value``
strings (mirrors ``batch.py``).
"""

from __future__ import annotations

from roboco.foundation.identity import Team

# A project-level conventions scaffold/restore PR carries no task and no org
# layer, so it gets a single conventional-commit-kind label (its branch is
# ``chore/roboco-conventions-scaffold``, its title ``chore(conventions): ...``).
CONVENTIONS_PR_LABELS: list[str] = ["chore"]


def _team_value(team: object | None) -> str:
    if team is None:
        return ""
    return str(getattr(team, "value", team)).lower()


def _layer_label(team: str, has_children: bool) -> str:
    """The owning-layer label for a task-bearing PR."""
    if team == Team.MAIN_PM.value:
        return "main-pm"
    if has_children:
        return f"cell/{team}"
    return f"subtask/{team}"


def derive_pr_labels(
    *,
    base_branch: str,
    is_root_pr: bool,
    task_team: object | None,
    batch_id: object | None,
    has_children: bool,
) -> list[str]:
    """The org-structure labels for a PR, in a stable order, de-duplicated.

    - ``to {base_branch}`` — the PR's REAL target branch, verbatim (e.g.
      ``to master``, ``to slave``, or any project-specific env-ladder rung) —
      never assumes a root PR targets "master" and everything else "slave".
    - ``root`` — an assembled root->master PR (``is_root_pr``).
    - ``MegaTask`` — the task carries a ``batch_id``.
    - layer label — ``main-pm`` for a Main-PM coordination root, ``cell/{team}``
      for a cell-assembled PR (``has_children``), else ``subtask/{team}`` for a
      leaf dev PR. Absent when the PR has no task (a freeform PR).
    """
    labels: list[str] = [f"to {base_branch}"]
    if is_root_pr:
        labels.append("root")
    if batch_id is not None:
        labels.append("MegaTask")
    team = _team_value(task_team)
    if team:
        labels.append(_layer_label(team, has_children))
    # de-dup preserving first-seen order (a MegaTask root PR can otherwise repeat)
    return list(dict.fromkeys(labels))
