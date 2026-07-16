"""Concrete remediation hints for tracing-gap and invalid-state responses.

Every hint is a single-sentence instruction with the exact next call the
agent should make. Hints are model-agnostic — no jargon, no API names the
agent doesn't already know from its prompt.
"""

from __future__ import annotations


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


def hint_for_open_findings(*, finding_ids: list[str], task_id: str) -> str:
    ids = ", ".join(finding_ids)
    return (
        f"open revision findings block i_am_done for task {task_id}: {ids}. "
        "Fix each, then call i_am_done(resolved_findings=[{'finding_id': "
        "'<id>', 'commit': '<sha>', 'note': '<what you changed>'}, ...]) "
        "naming every id (the id shown is the 8-char prefix from the "
        "'[F-xxxxxxxx]' rendering in your qa_notes/pm_notes/pr_reviewer_notes)."
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


def hint_for_short_doc_notes(*, min_chars: int) -> str:
    return (
        f"i_documented requires notes>="
        f"{min_chars} chars summarizing what you "
        "documented and where (file paths); pass a longer `notes` argument"
    )


def hint_for_missing_doc_files() -> str:
    return (
        "i_documented(files=[...]) requires the list of doc-file paths you "
        "committed; pass at least one path"
    )


def hint_for_short_dev_notes(*, min_chars: int, task_id: str) -> str:
    return (
        f"your dev_notes section is empty or under {min_chars} chars. Before "
        f"i_am_done, call note(scope='handoff', task_id='{task_id}', "
        "section={'summary': '<what you built, key changes, risks>'}) to fill "
        "it, then retry."
    )


def hint_for_short_pr_reviewer_notes(*, min_chars: int) -> str:
    return (
        f"your review note must be at least {min_chars} chars stating what you "
        "checked and the verdict rationale; pass a longer `body` (post_pr_review)"
        " / `notes` (pr_pass) / `issues` (pr_fail)."
    )


def hint_for_render_preview() -> str:
    return (
        "call request_render() to render this task's PR branch, Read every "
        "returned frame image, and verify each scene/feature from the brief "
        "appears fully and legibly — then retry i_am_done"
    )


def hint_for_short_quick_context(*, min_chars: int, task_id: str) -> str:
    return (
        f"your quick_context section is empty or under {min_chars} chars. Before "
        f"delegate, call note(scope='handoff', task_id='{task_id}', "
        "done='<state so far>', next='<what the cell should do>') to leave a "
        "resumption handoff (pass done and next as top-level string args, not "
        "nested in section), then retry."
    )
