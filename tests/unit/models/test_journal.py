"""roboco.models.journal coverage — entry factory functions.

Covers all create_*_entry helpers: happy paths, journal_id required guard,
and optional-field branches (how_applied, source, resolution, help_needed).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from roboco.models.base import JournalEntryType
from roboco.models.journal import (
    DecisionLogParams,
    GeneralEntryParams,
    LearningEntryParams,
    StruggleEntryParams,
    TaskReflectionParams,
    create_decision_log,
    create_general_entry,
    create_learning_entry,
    create_struggle_entry,
    create_task_reflection,
)

# ---------------------------------------------------------------------------
# create_task_reflection
# ---------------------------------------------------------------------------


def test_create_task_reflection_happy_path() -> None:
    journal_id = uuid4()
    task_id = uuid4()
    entry = create_task_reflection(
        TaskReflectionParams(
            task_id=task_id,
            title="Reflection",
            what_done="Implemented X",
            what_learned="Y is tricky",
            what_struggled="Z timing",
            next_steps=["Add tests", "Refactor"],
            journal_id=journal_id,
        )
    )
    assert entry.type == JournalEntryType.TASK_REFLECTION
    assert entry.journal_id == journal_id
    assert entry.task_id == task_id
    assert "Implemented X" in entry.content
    assert "[ ] Add tests" in entry.content


def test_create_task_reflection_requires_journal_id() -> None:
    with pytest.raises(ValueError, match="journal_id is required"):
        create_task_reflection(
            TaskReflectionParams(
                task_id=uuid4(),
                title="t",
                what_done="d",
                what_learned="l",
                what_struggled="s",
                next_steps=[],
            )
        )


# ---------------------------------------------------------------------------
# create_decision_log
# ---------------------------------------------------------------------------


def test_create_decision_log_happy_path() -> None:
    journal_id = uuid4()
    entry = create_decision_log(
        DecisionLogParams(
            title="Pick framework",
            context="We need a web framework",
            options=[
                {"name": "FastAPI", "pros": "fast", "cons": "newer"},
                {"name": "Django", "pros": "batteries", "cons": "heavy"},
            ],
            chosen="FastAPI",
            rationale="async-first",
            consequences=["Need uvicorn", "Async DB"],
            journal_id=journal_id,
        )
    )
    assert entry.type == JournalEntryType.DECISION_LOG
    assert "Option 1: FastAPI" in entry.content
    assert "Option 2: Django" in entry.content
    assert "Chose **FastAPI**" in entry.content


def test_create_decision_log_handles_missing_option_keys() -> None:
    """Options without name/pros/cons should default to placeholders."""
    entry = create_decision_log(
        DecisionLogParams(
            title="Pick",
            context="ctx",
            options=[{}, {}],
            chosen="X",
            rationale="r",
            consequences=[],
            journal_id=uuid4(),
        )
    )
    assert "Option 1" in entry.content
    assert "N/A" in entry.content


def test_create_decision_log_requires_journal_id() -> None:
    with pytest.raises(ValueError, match="journal_id is required"):
        create_decision_log(
            DecisionLogParams(
                title="t",
                context="c",
                options=[],
                chosen="x",
                rationale="r",
                consequences=[],
            )
        )


# ---------------------------------------------------------------------------
# create_learning_entry
# ---------------------------------------------------------------------------


def test_create_learning_entry_with_all_optionals() -> None:
    entry = create_learning_entry(
        LearningEntryParams(
            title="Learned async",
            what_learned="async/await",
            how_applied="Used in service layer",
            source="Real World Python book",
            journal_id=uuid4(),
        )
    )
    assert entry.type == JournalEntryType.LEARNING
    assert "How I Applied It" in entry.content
    assert "## Source" in entry.content
    assert entry.sentiment == "positive"


def test_create_learning_entry_without_optionals() -> None:
    entry = create_learning_entry(
        LearningEntryParams(
            title="L",
            what_learned="basic",
            journal_id=uuid4(),
        )
    )
    assert "How I Applied It" not in entry.content
    assert "## Source" not in entry.content


def test_create_learning_entry_requires_journal_id() -> None:
    with pytest.raises(ValueError, match="journal_id is required"):
        create_learning_entry(LearningEntryParams(title="t", what_learned="w"))


# ---------------------------------------------------------------------------
# create_struggle_entry
# ---------------------------------------------------------------------------


def test_create_struggle_entry_with_resolution_and_help() -> None:
    entry = create_struggle_entry(
        StruggleEntryParams(
            title="Bug",
            what_struggled="race condition",
            attempted_solutions=["lock", "queue"],
            resolution="Used asyncio.Lock",
            help_needed="None - solved",
            journal_id=uuid4(),
        )
    )
    assert entry.type == JournalEntryType.STRUGGLE
    assert "## Resolution" in entry.content
    assert "## Help Needed" in entry.content
    assert entry.sentiment == "frustrated"


def test_create_struggle_entry_without_optionals() -> None:
    entry = create_struggle_entry(
        StruggleEntryParams(
            title="x",
            what_struggled="y",
            attempted_solutions=["a"],
            journal_id=uuid4(),
        )
    )
    assert "## Resolution" not in entry.content
    assert "## Help Needed" not in entry.content


def test_create_struggle_entry_requires_journal_id() -> None:
    with pytest.raises(ValueError, match="journal_id is required"):
        create_struggle_entry(
            StruggleEntryParams(
                title="t",
                what_struggled="x",
                attempted_solutions=[],
            )
        )


# ---------------------------------------------------------------------------
# create_general_entry
# ---------------------------------------------------------------------------


def test_create_general_entry_happy_path() -> None:
    entry = create_general_entry(
        GeneralEntryParams(
            title="General note",
            content="Just thinking",
            is_private=True,
            journal_id=uuid4(),
        )
    )
    assert entry.type == JournalEntryType.GENERAL
    assert entry.is_private is True
    assert entry.content == "Just thinking"


def test_create_general_entry_requires_journal_id() -> None:
    with pytest.raises(ValueError, match="journal_id is required"):
        create_general_entry(GeneralEntryParams(title="t", content="c"))
