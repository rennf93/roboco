"""Concrete remediation hints for tracing-gap and invalid-state responses.

Every hint is a single-sentence instruction with the exact next call the
agent should make. Hints are model-agnostic — no jargon, no API names the
agent doesn't already know from its prompt.
"""

from __future__ import annotations


def hint_for_missing_plan(*, task_id: str) -> str:
    return (
        f"call i_will_work_on(task_id='{task_id}', plan='<one-paragraph "
        f"plan describing what you will do>')"
    )


def hint_for_missing_progress() -> str:
    return (
        "make at least one commit (use commit(message=...)) before i_am_done; "
        "the commit auto-creates a progress entry"
    )


def hint_for_missing_reflect(*, task_id: str) -> str:
    return (
        f"call note(scope='reflect', task_id='{task_id}', "
        f"text='<reflection summarizing what you did and why>')"
    )


def hint_for_unaddressed_acceptance_criteria(
    *, criteria: list[str], task_id: str
) -> str:
    bullets = "; ".join(f'"{c}"' for c in criteria)
    return (
        f"every acceptance criterion needs a referencing artifact (commit / "
        f"progress entry / note). Unaddressed for task {task_id}: {bullets}. "
        f"Add notes or commits referencing each before i_am_done."
    )


def hint_for_unread_a2a(*, count: int, task_id: str) -> str:
    return (
        f"you have {count} unread A2A message(s) about task {task_id}; "
        f"read them via context_briefing.unread_a2a in your next verb response, "
        f"then proceed"
    )


def hint_for_missing_journal_decision() -> str:
    return (
        "call note(scope='decision', text='<your decision and rationale>') "
        "before complete"
    )


def hint_for_missing_journal_learning() -> str:
    return (
        "call note(scope='learning', text='<what this review revealed>') "
        "before pass/fail"
    )


def hint_for_missing_qa_notes() -> str:
    return (
        "qa_notes must be at least 80 chars describing what you reviewed and the "
        "outcome rationale; pass it via pass(notes=...) or fail(issues=...)"
    )


def hint_for_evidence_not_inspected(*, task_id: str) -> str:
    return f"call evidence(task_id='{task_id}') to inspect the PR before pass/fail"
