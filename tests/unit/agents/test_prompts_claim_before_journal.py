"""Agent prompts must never order a journal write before the claim it needs.

The content-write gate refuses ``note`` (and ``dm`` / ``notify``) on a task
the writer has not claimed, yet the PM prompts' Workflow told the PM to
``note(scope='decision', ...)`` on its pending root and only then call
``i_will_plan``. Commit 66bd6290 fixed the State -> Verb tables but left the
Workflow steps, so main-pm kept paying a refused note per fresh root (7 on
2026-10-28 before that commit, 0c4e2592 and 14c9875a on 2026-11-01 after
it) and the cell PMs did the same on theirs. This grep invariant keeps every
prompt in claim-then-journal order:

* a chained instruction (``a → b → c``) never puts a ``note(`` step before
  an ``i_will_plan(`` / ``i_will_work_on(`` step;
* a numbered step that is a ``note(`` call is never followed by a step that
  is the claim call;
* no prompt asks for a journal entry "before every" claim.

The agent-facing RAG corpus (``docs/rag``) is held to the same order.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PROMPTS = REPO / "agents" / "prompts"
RAG_DOCS = REPO / "docs" / "rag"
CLAIM_CALL = re.compile(r"\bi_will_(?:plan|work_on)\(")
NOTE_CALL = re.compile(r"\bnote\(")
NUMBERED_STEP = re.compile(r"^\s*\d+\.\s+`?(?P<call>\w+)\(")
ARROW = re.compile(r"→|->")
BEFORE_EVERY_CLAIM = re.compile(
    r"before (?:every|each) `?i_will_(?:plan|work_on)", re.IGNORECASE
)
CHECKED_PROMPTS = (
    "base.md",
    "roles/main_pm.md",
    "roles/cell_pm.md",
)


def _checked_documents() -> list[Path]:
    """The role prompts plus the agent-facing RAG corpus the agents also read."""
    return [PROMPTS / prompt for prompt in CHECKED_PROMPTS] + sorted(
        RAG_DOCS.rglob("*.md")
    )


def _chained_note_before_claim(line: str) -> bool:
    """True when an arrow chain on ``line`` has a note step before the claim."""
    seen_note = False
    for step in ARROW.split(line):
        if CLAIM_CALL.search(step):
            return seen_note
        seen_note = seen_note or bool(NOTE_CALL.search(step))
    return False


def _numbered_note_then_claim(lines: list[str]) -> list[int]:
    """1-based line numbers of a numbered note step followed by the claim step."""
    calls = [
        (number, match.group("call"))
        for number, line in enumerate(lines, start=1)
        if (match := NUMBERED_STEP.match(line))
    ]
    return [
        number
        for (number, call), (_, next_call) in itertools.pairwise(calls)
        if call == "note" and next_call.startswith("i_will_")
    ]


def _journal_before_claim_offences(text: str) -> list[str]:
    lines = text.splitlines()
    offences = [
        f"line {number}: chained note before the claim"
        for number, line in enumerate(lines, start=1)
        if _chained_note_before_claim(line)
    ]
    offences += [
        f"line {number}: numbered note step before the claim step"
        for number in _numbered_note_then_claim(lines)
    ]
    offences += [
        f"line {number}: journal entry demanded before every claim"
        for number, line in enumerate(lines, start=1)
        if BEFORE_EVERY_CLAIM.search(line)
    ]
    return offences


@pytest.mark.parametrize(
    "document", _checked_documents(), ids=lambda path: str(path.relative_to(REPO))
)
def test_document_claims_before_it_journals(document: Path) -> None:
    offences = _journal_before_claim_offences(document.read_text())
    assert not offences, f"{document.relative_to(REPO)} writes first: {offences}"


def test_the_checker_flags_each_pre_claim_shape() -> None:
    chained = (
        "| `pending` | `evidence(id)` → `note(scope='decision')` → `i_will_plan(id)` |"
    )
    numbered = (
        "3. `note(scope='decision', task_id='r')` - why.\n4. `i_will_plan(task_id='r')`"
    )
    cadence = "| `decision` | Before every `i_will_work_on` | `note(...)` |"
    claim_first = "| `pending` | `i_will_plan(id)` → `note(scope='decision')` |"

    assert _journal_before_claim_offences(chained)
    assert _journal_before_claim_offences(numbered)
    assert _journal_before_claim_offences(cadence)
    assert not _journal_before_claim_offences(claim_first)
