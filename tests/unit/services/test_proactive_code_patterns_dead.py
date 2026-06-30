"""The code-patterns surface is vestigial — build_context_package must never
populate it and the summary must never advertise it (#382).

Code indexing was removed, so ``ContextPackage.code_patterns`` is a permanently
empty slot. These tests pin that ``on_task_claimed`` leaves it empty and the
generated summary omits the code-patterns line, so no consumer can branch on a
field the system claims to populate but doesn't.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from roboco.models.optimal import IndexType, SearchResult
from roboco.services.proactive import ContextPackage, ProactiveKnowledgeService


def _result(content: str) -> SearchResult:
    return SearchResult(
        content=content,
        source="test",
        score=1.0,
        index_type=IndexType.JOURNALS,
        metadata={},
    )


class _StubOptimal:
    """Minimal stand-in returning one item per search surface."""

    async def search(self, **_: Any) -> list[SearchResult]:
        return [_result("similar")]

    async def search_learnings(self, **_: Any) -> list[SearchResult]:
        return [_result("learning")]

    async def get_standards(self, **_: Any) -> list[SearchResult]:
        return [_result("standard")]

    async def search_errors(self, **_: Any) -> list[SearchResult]:
        return [_result("issue")]


@pytest.mark.asyncio
async def test_on_task_claimed_never_populates_code_patterns() -> None:
    service = ProactiveKnowledgeService()
    await service.initialize(_StubOptimal())

    package = await service.on_task_claimed(
        task_id=uuid4(),
        agent_id=uuid4(),
        task_title="Add auth endpoint",
        task_description="Implement login",
        task_type="feature",
    )

    assert package.code_patterns == []


@pytest.mark.asyncio
async def test_summary_omits_code_patterns_line() -> None:
    service = ProactiveKnowledgeService()
    await service.initialize(_StubOptimal())

    package = await service.on_task_claimed(
        task_id=uuid4(),
        agent_id=uuid4(),
        task_title="Add auth endpoint",
        task_description="Implement login",
        task_type="feature",
    )

    assert "code patterns" not in package.summary


def test_context_package_field_back_compat_empty() -> None:
    """The deprecated field stays present and default-empty for API back-compat."""
    pkg = ContextPackage()
    assert pkg.code_patterns == []
    assert "code_patterns" in pkg.to_dict()
