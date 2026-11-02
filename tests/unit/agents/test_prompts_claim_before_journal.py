"""Agent prompts must never order a journal write before the claim it needs.

The content-write gate refuses ``note`` (and ``dm`` / ``notify``) on a task
the writer has not claimed, yet the PM prompts' Workflow told the PM to
``note(scope='decision', ...)`` on its pending root and only then call
``i_will_plan``. Commit 66bd6290 fixed the State -> Verb tables but left the
Workflow steps, so main-pm kept paying a refused note per fresh root (7 on
2026-10-28 before that commit, 0c4e2592 and 14c9875a on 2026-11-01 after
it) and the cell PMs did the same on theirs; the developer prompt prescribed
the same order before ``i_will_work_on`` (dfeb2d8a, 86b799b8, 0f391d46).
This grep invariant keeps every prompt in claim-then-journal order:

* a chained instruction (``a → b → c``) never puts a ``note(`` step before
  an ``i_will_plan(`` / ``i_will_work_on(`` step;
* a numbered step that is a ``note(`` call is never followed by a step that
  is the claim call;
* no prompt asks for a journal entry "before every" claim.

The agent-facing RAG corpus (``docs/rag``) is held to the same order.

The same documents must not prescribe a decision note that the verb records
itself. ``delegate``, ``complete``, ``submit_up``, ``submit_root``,
``unblock``, ``escalate_up``, ``escalate_to_ceo``, ``cancel_leaf`` and
``request_changes`` write their own rationale (subtask, ``notes``,
``reason``, findings) as the caller's ``journal:decision`` through
``_ensure_pm_decision`` before their decision gate runs. A separate
``note(scope='decision')`` first is a wasted turn at best, and it is
refused when the caller does not hold the task's claim: on 2026-10-28 the
cell PMs and main-pm paid ten refused decision notes right before an
``unblock`` or ``escalate_up`` that then succeeded on its own ``reason``
(11a83407, 86ed0eab, f5d66db6, 927fb466, d6a26717). The check covers
arrow chains, numbered steps, fenced Python examples, "required before" /
"before every" prose, "gateway-required" decision notes, and a verb table's
precondition cell demanding a recorded decision.
"""

from __future__ import annotations

import ast
import itertools
import re
from pathlib import Path
from typing import Any

import pytest
from roboco.foundation.policy import tracing
from roboco.runtime.engines.dispatch_prompts_management import (
    ManagementPromptsEngine,
)

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


def _checked_documents() -> list[Path]:
    """Every agent prompt plus the agent-facing RAG corpus the agents read."""
    return sorted(PROMPTS.rglob("*.md")) + sorted(RAG_DOCS.rglob("*.md"))


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


CHOREOGRAPHER = REPO / "roboco" / "services" / "gateway" / "choreographer"
# The choreographer functions that record a decision from the verb's own
# rationale (``_ensure_pm_decision``), and the verbs each one serves.
DECISION_RECORDING_SITES: dict[str, frozenset[str]] = {
    "delegate": frozenset({"delegate"}),
    "unblock": frozenset({"unblock"}),
    "escalate_up": frozenset({"escalate_up"}),
    "escalate_to_ceo": frozenset({"escalate_to_ceo"}),
    "cancel_leaf": frozenset({"cancel_leaf"}),
    "_record_request_changes_decision": frozenset({"request_changes"}),
    "_cell_pm_complete_guard": frozenset({"complete"}),
    "_main_pm_complete_guard": frozenset({"complete"}),
    "_submit_up_state_guard": frozenset({"submit_up", "submit_root"}),
    "_post_claim_journal_gate": frozenset({"i_will_plan"}),
}
SELF_RECORDING_VERBS: frozenset[str] = frozenset().union(
    *DECISION_RECORDING_SITES.values()
)
_VERB_NAMES = "|".join(sorted(SELF_RECORDING_VERBS))
DECISION_NOTE = re.compile(r"""\bnote\([^)\n]*scope=['"]decision['"]""")
FIRST_CALL = re.compile(r"\b(?P<name>\w+)\(")
NUMBERED_LINE = re.compile(r"^\s*\d+\.\s")
DEMANDED_BEFORE_VERB = re.compile(
    rf"(?:required:?[^|]*?\bbefore|before every|before each)\b[^|]*?"
    rf"`?\b(?:{_VERB_NAMES}|escalate_\*)",
    re.IGNORECASE,
)
SELF_RECORDING_CALL = re.compile(rf"\b(?:{_VERB_NAMES})\(")
AFTER_DECISION_NOTE = re.compile(
    r"""\b(?:after|once)\b[^|]*?\bnote\([^)\n]*scope=['"]decision""",
    re.IGNORECASE,
)
GATEWAY_DEMANDED = re.compile(r"gateway-(?:required|enforced)", re.IGNORECASE)
RECORDED_DECISION_PRECONDITION = re.compile(
    r"decision`?\s+(?:recorded|written|entry|note)", re.IGNORECASE
)
VERB_TABLE_ROW = re.compile(rf"^\|\s*`(?:{_VERB_NAMES})\(")
FENCED_PYTHON = re.compile(r"```python\n(?P<code>.*?)```", re.DOTALL)


def _functions_recording_a_pm_decision() -> set[str]:
    """Every choreographer function that calls ``_ensure_pm_decision``."""
    names: set[str] = set()
    for path in sorted(CHOREOGRAPHER.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_ensure_pm_decision"
                for call in ast.walk(node)
            ):
                names.add(node.name)
    return names


def _first_call(text: str) -> str | None:
    match = FIRST_CALL.search(text)
    return match.group("name") if match else None


def _chained_decision_before_recording_verb(line: str) -> bool:
    """True when an arrow chain has a decision note right before such a verb."""
    calls = [_first_call(step) for step in ARROW.split(line)]
    steps = [
        (bool(DECISION_NOTE.search(step)), call)
        for step, call in zip(ARROW.split(line), calls, strict=True)
        if call
    ]
    return any(
        is_decision and next_call in SELF_RECORDING_VERBS
        for (is_decision, _), (_, next_call) in itertools.pairwise(steps)
    )


def _numbered_step_blocks(lines: list[str]) -> list[tuple[int, str]]:
    """Each numbered step with its continuation lines, by its first line number.

    A step runs until the next numbered line or a blank line, so a spawn
    prompt's multi-line call (``2. note(scope='decision', ...`` over three
    lines, then ``— REQUIRED before submit_up()``) is one step.
    """
    blocks: list[tuple[int, list[str]]] = []
    for number, line in enumerate(lines, start=1):
        if NUMBERED_LINE.match(line):
            blocks.append((number, [line]))
        elif blocks and line.strip() and blocks[-1][0] + len(blocks[-1][1]) == number:
            blocks[-1][1].append(line)
    return [(number, "\n".join(block)) for number, block in blocks]


def _numbered_decision_then_recording_verb(lines: list[str]) -> list[int]:
    """Line numbers of a decision-note step that precedes or demands such a verb."""
    steps = _numbered_step_blocks(lines)
    followed = [
        number
        for (number, step), (_, next_step) in itertools.pairwise(steps)
        if DECISION_NOTE.search(step) and _first_call(next_step) in SELF_RECORDING_VERBS
    ]
    demanding = [
        number
        for number, step in steps
        if DECISION_NOTE.search(step) and DEMANDED_BEFORE_VERB.search(step)
    ]
    return sorted(set(followed + demanding))


def _demands_decision_before_recording_verb(line: str) -> bool:
    """True for prose that makes a decision note a precondition of such a verb."""
    if "decision" not in line.lower():
        return False
    if DEMANDED_BEFORE_VERB.search(line):
        return True
    if DECISION_NOTE.search(line) and GATEWAY_DEMANDED.search(line):
        return True
    if SELF_RECORDING_CALL.search(line) and AFTER_DECISION_NOTE.search(line):
        return True
    if VERB_TABLE_ROW.match(line):
        precondition = line.rstrip().rstrip("|").rsplit("|", 1)[-1]
        return bool(RECORDED_DECISION_PRECONDITION.search(precondition))
    return False


def _called_name(statement: ast.stmt) -> tuple[str, ast.Call] | None:
    value = getattr(statement, "value", None)
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id, value
    return None


def _is_decision_note(statement: ast.stmt) -> bool:
    called = _called_name(statement)
    return (
        called is not None
        and called[0] == "note"
        and any(
            keyword.arg == "scope"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "decision"
            for keyword in called[1].keywords
        )
    )


def _example_decision_before_recording_verb(code: str) -> bool:
    """True when a Python example calls a decision note right before such a verb."""
    try:
        statements = ast.parse(code).body
    except SyntaxError:
        return False
    return any(
        _is_decision_note(statement)
        and (called := _called_name(following)) is not None
        and called[0] in SELF_RECORDING_VERBS
        for statement, following in itertools.pairwise(statements)
    )


def _self_recorded_decision_offences(text: str) -> list[str]:
    lines = text.splitlines()
    offences = [
        f"line {number}: chained decision note before a self-recording verb"
        for number, line in enumerate(lines, start=1)
        if _chained_decision_before_recording_verb(line)
    ]
    offences += [
        f"line {number}: numbered decision-note step before a self-recording verb"
        for number in _numbered_decision_then_recording_verb(lines)
    ]
    offences += [
        f"line {number}: decision note demanded before a self-recording verb"
        for number, line in enumerate(lines, start=1)
        if _demands_decision_before_recording_verb(line)
    ]
    offences += [
        "python example: decision note right before a self-recording verb"
        for block in FENCED_PYTHON.finditer(text)
        if _example_decision_before_recording_verb(block.group("code"))
    ]
    return offences


def test_the_self_recording_verbs_match_the_choreographer() -> None:
    assert _functions_recording_a_pm_decision() == set(DECISION_RECORDING_SITES)


@pytest.mark.parametrize(
    "document", _checked_documents(), ids=lambda path: str(path.relative_to(REPO))
)
def test_document_leaves_self_recorded_decisions_to_the_verb(document: Path) -> None:
    offences = _self_recorded_decision_offences(document.read_text())
    assert not offences, (
        f"{document.relative_to(REPO)} asks for a decision note the verb "
        f"records itself: {offences}"
    )


def test_the_checker_flags_each_self_recorded_decision_shape() -> None:
    chained = (
        "| `blocked` | `note(scope='decision', ...)` → `unblock(task_id, reason)` |"
    )
    numbered = (
        "3. `note(scope='decision', task_id=...)` — why.\n"
        "4. If it is CEO-worthy: `escalate_to_ceo(task_id, reason='...')`."
    )
    spawn_step = (
        "3. note(scope='decision', task_id=\"t\",\n"
        '        text="Closure: <rationale>")\n'
        "   — REQUIRED before submit_up().\n"
    )
    required = "| `note(...)` | Required: `scope='decision'` before `delegate`. | |"
    gateway = (
        "6. ✅ `note(scope='decision', task_id=root_id)` written (gateway-required)."
    )
    precondition = (
        "| `complete(task_id, notes)` | Merges. | journal `decision` recorded. |"
    )
    after = "- `escalate_to_ceo(task_id, reason)` (after `note(scope='decision', ...)`)"
    example = (
        "```python\nnote(text='why', scope='decision')\n"
        "request_changes(task_id='t', findings=[])\n```"
    )
    left_to_the_verb = (
        "| `blocked` | `unblock(task_id, reason='...')` (records `reason` as "
        "your decision; no separate note) |"
    )
    reflect_first = "| `in_progress` | `note(scope='reflect', ...)` → `complete(id)` |"
    unrelated_requirement = (
        "If a required cell is unnecessary, record why in "
        "`note(scope='decision', ...)` and `dm`/`escalate_up` to confirm."
    )

    shapes = (
        chained,
        numbered,
        spawn_step,
        required,
        gateway,
        precondition,
        after,
        example,
    )
    for shape in shapes:
        assert _self_recorded_decision_offences(shape), shape
    assert not _self_recorded_decision_offences(left_to_the_verb)
    assert not _self_recorded_decision_offences(reflect_first)
    assert not _self_recorded_decision_offences(unrelated_requirement)


UNBLOCK_WITHOUT_REASON = re.compile(r"\bunblock\((?![^)]*\breason\b)[^)]*\)")


def _unblock_without_reason_offences(text: str) -> list[str]:
    """``unblock`` records its required ``reason`` as the decision."""
    return [
        f"unblock call without its reason: {match.group(0)!r}"
        for match in UNBLOCK_WITHOUT_REASON.finditer(text)
    ]


def _spawn_prompts() -> dict[str, str]:
    """The spawn prompts the orchestrator hands the PMs and the board.

    The builders read nothing off the orchestrator, so the engine mixin is
    built bare; typed as a plain class because it only derives from the
    orchestrator protocol under type checking.
    """
    engine_class: Any = ManagementPromptsEngine
    engine = engine_class.__new__(engine_class)
    root = {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Ship the thing",
        "team": "backend",
        "description": "What the root asks for.",
        "project_slug": "roboco-api",
    }
    child = {**root, "parent_task_id": "22222222-2222-2222-2222-222222222222"}
    blocked = {**child, "blocker": {"reason": "stuck", "what_needed": "a hand"}}
    return {
        "main_pm_triage": engine._build_main_pm_triage_prompt(root),
        "cell_pm_triage": engine._build_pm_triage_prompt(child),
        "root_closure": engine._build_pm_closure_prompt(root, []),
        "cell_closure": engine._build_pm_closure_prompt(child, []),
        "pm_review": engine._build_pm_review_prompt(child),
        "pm_blocker": engine._build_pm_blocker_prompt(blocked),
        "board": engine._build_board_prompt(root),
    }


def test_documents_give_unblock_its_reason() -> None:
    offences = {
        str(document.relative_to(REPO)): found
        for document in _checked_documents()
        if (found := _unblock_without_reason_offences(document.read_text()))
    }
    assert not offences, offences


@pytest.mark.parametrize("name", sorted(_spawn_prompts()))
def test_spawn_prompt_claims_first_and_leaves_decisions_to_the_verbs(
    name: str,
) -> None:
    """The PM spawn prompts told the PM to ``note(scope='decision')`` and
    only then ``i_will_plan``, "REQUIRED before i_will_plan / complete /
    escalate", and to leave a decision note instead of ``request_changes``
    on a review that needs rework; the role prompts were fixed but these
    templates were not."""
    prompt = _spawn_prompts()[name]
    offences = (
        _journal_before_claim_offences(prompt)
        + _self_recorded_decision_offences(prompt)
        + _unblock_without_reason_offences(prompt)
    )
    assert not offences, f"{name} spawn prompt: {offences}"


def test_every_verb_requiring_a_decision_records_it_itself() -> None:
    """The prompts and the ``journal:decision`` remediation tell the agent
    the verb records its own rationale; that holds only while every verb
    declaring the requirement is one of the self-recording verbs."""
    declaring = {
        verb
        for verb, requirements in tracing.VERB_REQUIREMENTS.items()
        if tracing.Requirement.JOURNAL_DECISION in requirements
    }
    assert declaring == SELF_RECORDING_VERBS - {"i_will_plan"}
