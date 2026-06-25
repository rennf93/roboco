"""MemoryDistiller — a local-LLM distilled completion lesson (best-effort)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from roboco.services.memory_distiller import LessonInput, MemoryDistiller


def _input() -> LessonInput:
    return LessonInput(
        title="Add a retry to the flaky pg fixture",
        acceptance_criteria=["The pg test passes 100 times in a row"],
        dev_notes="Wrapped the connect in a 3x retry with backoff.",
        qa_notes="Confirmed stable across 200 runs.",
        commit_messages=["fix: retry pg connect", "test: stress the fixture"],
    )


@pytest.mark.asyncio
async def test_distill_returns_lesson(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "roboco.services.memory_distiller._chat",
        AsyncMock(
            return_value="Problem: flaky pg. Approach: retry+backoff. Gotcha: reset."
        ),
    )
    out = await MemoryDistiller().distill(_input())
    assert out is not None
    assert "Gotcha" in out


@pytest.mark.asyncio
async def test_distill_none_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "roboco.services.memory_distiller._chat", AsyncMock(side_effect=RuntimeError)
    )
    assert await MemoryDistiller().distill(_input()) is None


@pytest.mark.asyncio
async def test_distill_none_on_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "roboco.services.memory_distiller._chat", AsyncMock(return_value="   ")
    )
    assert await MemoryDistiller().distill(_input()) is None


@pytest.mark.asyncio
async def test_distill_caps_at_120_words(monkeypatch: pytest.MonkeyPatch) -> None:
    long_lesson = " ".join(f"word{i}" for i in range(300))
    monkeypatch.setattr(
        "roboco.services.memory_distiller._chat",
        AsyncMock(return_value=long_lesson),
    )
    out = await MemoryDistiller().distill(_input())
    assert out is not None
    assert len(out.split()) <= 120  # noqa: PLR2004 - the documented word budget
