"""What the trails bridge writes to Entire, rendered from RoboCo's rows."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from roboco.services.entire_trails.render import (
    TreeRow,
    addressed_finding_body,
    entire_severity,
    finding_body,
    trail_body,
    trail_fields,
)


def _task(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "id": uuid4(),
        "title": "Add rate limiting",
        "description": "Rate-limit the public API.",
        "acceptance_criteria": ["429 after 100 requests a minute"],
        "plan": None,
        "status": "in_progress",
        "task_type": "code",
        "priority": 1,
        "team": "backend",
        "branch_name": None,
        "pr_number": None,
        "pr_url": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _finding(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "id": uuid4(),
        "criterion": "429 after 100 requests a minute",
        "severity": "major",
        "origin": "qa",
        "round": 2,
        "expected": "429 on request 101",
        "actual": "200 on request 101",
        "fix": "count per client, not per process",
        "evidence": "curl loop log",
        "author_slug": "be-qa",
        "addressed_by_commit": None,
        "resolution_note": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [("blocker", "high"), ("major", "high"), ("minor", "medium"), ("nit", "low")],
)
def test_severities_map_onto_entires_three(ours: str, theirs: str) -> None:
    assert entire_severity(ours) == theirs


def test_a_findings_text_carries_every_ledger_field_and_its_id() -> None:
    task, finding = _task(), _finding()
    body = finding_body(finding, task)
    for text in (
        finding.criterion,
        "major, qa review, round 2",
        "Expected: 429 on request 101",
        "Actual: 200 on request 101",
        "Fix: count per client, not per process",
        "curl loop log",
        str(finding.id),
        "be-qa",
        f"[{str(task.id)[:8]}] Add rate limiting",
    ):
        assert text in body


def test_a_findings_text_is_the_same_every_time_it_is_rendered() -> None:
    """Entire refuses a client-id retry whose fields differ, so the same row
    must always render the same text."""
    task, finding = _task(), _finding()
    assert finding_body(finding, task) == finding_body(finding, task)


def test_an_addressed_finding_says_where_it_was_fixed() -> None:
    finding = _finding(addressed_by_commit="abc1234", resolution_note="per client")
    task = _task()
    body = addressed_finding_body(finding, task)
    assert body.startswith(finding_body(finding, task))
    assert body.endswith("Addressed in abc1234: per client")


def test_a_trail_shows_intent_plan_and_the_task_table() -> None:
    root = _task(
        plan={
            "approach": "Token bucket in Redis.",
            "sub_tasks": [
                {"title": "Bucket", "completed": True},
                {"title": "Middleware", "completed": False},
            ],
            "risks": [{"description": "Redis down", "mitigation": "fail open"}],
            "open_questions": [{"question": "Per key or per IP?", "answer": "key"}],
        }
    )
    child = _task(
        title="Bucket | store",
        status="awaiting_qa",
        branch_name="feature/backend/a--b",
        pr_number=12,
        pr_url="https://github.com/acme/app/pull/12",
    )
    body = trail_body(root, [TreeRow(task=cast("Any", child), depth=1)])
    for text in (
        "Rate-limit the public API.",
        "- 429 after 100 requests a minute",
        "Token bucket in Redis.",
        "- [x] Bucket",
        "- [ ] Middleware",
        "Redis down (mitigation: fail open)",
        "Per key or per IP? (answer: key)",
        "Bucket \\| store",
        "`feature/backend/a--b`",
        "[#12](https://github.com/acme/app/pull/12)",
        str(root.id),
    ):
        assert text in body


def test_a_plain_string_plan_is_shown_as_written() -> None:
    assert "Just do it." in trail_body(_task(plan="Just do it."), [])


def test_a_closing_trail_states_the_outcome() -> None:
    body = trail_body(_task(), [], closing_note="The task ended completed.")
    assert "## Outcome\n\nThe task ended completed." in body


def test_trail_fields_map_type_and_priority() -> None:
    fields = trail_fields(_task(task_type="research", priority=0), [])
    assert fields["type"] == "task"
    assert fields["priority"] == "urgent"
    assert trail_fields(_task(), [])["type"] == "feature"
    assert set(fields) == {"title", "body", "type", "priority"}
