"""i_documented synthesizes the required journal:reflect from the documenter's
own submission so the reflect gate passes in one call.

Before this, i_documented returned a by-design tracing_gap ("journal:reflect
missing") on the first call. That rejection counts toward the per-verb circuit
breaker (limit 3 / 60s), so a documenter that fumbled note(scope='reflect')
even twice got locked out and went idle, stranding the task in
awaiting_documentation. The notes + files i_documented already carries are the
reflection's substance, so _ensure_doc_reflect writes the entry from them —
but only when the submission is substantive and the agent didn't journal one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
        "messaging": AsyncMock(),
    }
    base.update(overrides)
    return ChoreographerDeps(**base)


_ADEQUATE_NOTES = "Documented the auth flow in README and the API reference."
_SHORT_NOTES = "done"  # below docs_notes_min_chars (20)
_FILES = ["README.md", "docs/api.md"]


@pytest.mark.asyncio
async def test_writes_reflect_from_submission_when_absent() -> None:
    journal = AsyncMock()
    journal.has_reflect_for_task.return_value = False
    c = Choreographer(_make_deps(journal=journal))
    doc_id, task_id = uuid4(), uuid4()

    await c._ensure_doc_reflect(doc_id, task_id, _ADEQUATE_NOTES, _FILES)

    journal.write_entry.assert_awaited_once()
    kwargs = journal.write_entry.await_args.kwargs
    assert kwargs["scope"] == "reflect"
    assert kwargs["agent_id"] == doc_id
    assert kwargs["task_id"] == task_id
    assert _ADEQUATE_NOTES in kwargs["content"]
    assert "README.md" in kwargs["content"]


@pytest.mark.asyncio
async def test_skips_when_agent_already_authored_reflect() -> None:
    journal = AsyncMock()
    journal.has_reflect_for_task.return_value = True
    c = Choreographer(_make_deps(journal=journal))

    await c._ensure_doc_reflect(uuid4(), uuid4(), _ADEQUATE_NOTES, _FILES)

    journal.write_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_notes_below_threshold() -> None:
    journal = AsyncMock()
    journal.has_reflect_for_task.return_value = False
    c = Choreographer(_make_deps(journal=journal))

    await c._ensure_doc_reflect(uuid4(), uuid4(), _SHORT_NOTES, _FILES)

    journal.write_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_no_files() -> None:
    journal = AsyncMock()
    journal.has_reflect_for_task.return_value = False
    c = Choreographer(_make_deps(journal=journal))

    await c._ensure_doc_reflect(uuid4(), uuid4(), _ADEQUATE_NOTES, [])

    journal.write_entry.assert_not_awaited()
