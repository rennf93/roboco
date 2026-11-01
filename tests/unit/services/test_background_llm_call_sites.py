"""Every orchestrator-side text-LLM call site goes through the background LLM
client.

Each site runs for real with the routing mode on Ollama, a fake Ollama that
answers HTTP 402 (the 2026-10-28 incident) and a fake Haiku: the site must
come back with Haiku's text, and the client's fallback log must name the
site's purpose. A structural test also proves no module under ``roboco/``
still reaches Ollama's chat endpoint on its own.
"""

from __future__ import annotations

import ast
from http import HTTPStatus
from pathlib import Path

import pytest
from roboco.eval import runner as eval_runner
from roboco.models.optimal import IndexType, SearchResult
from roboco.services import (
    memory_distiller,
    vault_intake_engine,
    video_engine,
    x_engine,
)
from roboco.services.optimal import OptimalService
from roboco.services.optimal_brain.indexes.journals import JournalsIndexPlugin
from roboco.services.optimal_brain.mentor import MentorService
from roboco.services.optimal_brain.validator import (
    VALIDATION_SYSTEM_PROMPT,
    ValidatorService,
)
from tests.unit.services.background_llm_fakes import (
    FakeHaiku,
    FakeOllama,
    LogRecorder,
    follow_routing_mode,
    record_client_logs,
    serve_fake_haiku,
    serve_fake_ollama,
)

_ROBOCO_PACKAGE = Path(__file__).resolve().parents[3] / "roboco"
# The only modules allowed to name Ollama's chat endpoint setting: where it is
# declared, and the client that uses it.
_MODULES_THAT_MAY_NAME_THE_OLLAMA_ENDPOINT = {
    _ROBOCO_PACKAGE / "config.py",
    _ROBOCO_PACKAGE / "services" / "background_llm.py",
}
_HEALTH_TIMEOUT_SECONDS = 5.0


@pytest.fixture
def haiku(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeHaiku:
    """Ollama mode, Ollama out of credit, Haiku answering."""
    follow_routing_mode(monkeypatch, "ollama")
    serve_fake_ollama(monkeypatch, FakeOllama(status=HTTPStatus.PAYMENT_REQUIRED))
    fake = FakeHaiku(answer="Problem: x. Approach: y. Gotcha: z.")
    serve_fake_haiku(monkeypatch, fake, tmp_path)
    return fake


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> LogRecorder:
    return record_client_logs(monkeypatch)


def _fallback_purposes(logs: LogRecorder) -> list[str]:
    return [event["purpose"] for event in logs.fallback_events()]


def _citation() -> SearchResult:
    return SearchResult(
        content="Use a retry with backoff around the connect.",
        source="docs/rag/standards.md",
        score=0.9,
        index_type=IndexType.STANDARDS,
    )


def _attribute_reads_of(path: Path, attribute: str) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == attribute
    ]


def test_no_module_reaches_the_ollama_chat_endpoint_on_its_own() -> None:
    offenders = {
        f"{path.relative_to(_ROBOCO_PACKAGE.parent)}:{line}"
        for path in _ROBOCO_PACKAGE.rglob("*.py")
        if path not in _MODULES_THAT_MAY_NAME_THE_OLLAMA_ENDPOINT
        for attribute in ("local_llm_base_url", "local_llm_model")
        for line in _attribute_reads_of(path, attribute)
    }
    assert offenders == set()


async def test_rag_synthesis(haiku: FakeHaiku, logs: LogRecorder) -> None:
    answer = await OptimalService()._synthesize_from_citations(
        "How do I stop a flaky connect?", [_citation()]
    )

    assert answer == haiku.answer
    assert _fallback_purposes(logs) == ["rag.synthesis"]


async def test_rag_synthesis_with_no_backend_returns_the_citation_extract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    serve_fake_haiku(monkeypatch, FakeHaiku(answer=None), tmp_path)

    answer = await OptimalService()._synthesize_from_citations(
        "How do I stop a flaky connect?", [_citation()]
    )

    assert "Here's what I found in the knowledge base" in answer
    assert "retry with backoff" in answer


@pytest.mark.usefixtures("haiku")
async def test_rag_health_check_reports_the_backend_that_answered(
    logs: LogRecorder,
) -> None:
    details: dict[str, object] = {}

    healthy = await OptimalService()._check_llm_health(details, _HEALTH_TIMEOUT_SECONDS)

    assert healthy is True
    assert details["llm_backend"] == "claude"
    assert "HTTP 402" in str(details["llm_fallback_reason"])
    assert _fallback_purposes(logs) == ["rag.health_check"]


async def test_rag_health_check_with_no_backend_reports_every_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follow_routing_mode(monkeypatch, "ollama")
    serve_fake_ollama(monkeypatch, FakeOllama(status=HTTPStatus.PAYMENT_REQUIRED))
    details: dict[str, object] = {}

    healthy = await OptimalService()._check_llm_health(details, _HEALTH_TIMEOUT_SECONDS)

    assert healthy is False
    assert "ollama" in str(details["llm_error"])
    assert "claude" in str(details["llm_error"])


async def test_memory_distiller(haiku: FakeHaiku, logs: LogRecorder) -> None:
    lesson = await memory_distiller.MemoryDistiller().distill(
        memory_distiller.LessonInput(title="Add a retry to the flaky pg fixture")
    )

    assert lesson == haiku.answer
    assert _fallback_purposes(logs) == ["memory_distiller.lesson"]


async def test_vault_intake_extraction(haiku: FakeHaiku, logs: LogRecorder) -> None:
    assert await vault_intake_engine._chat("Extract a task draft.") == haiku.answer
    assert _fallback_purposes(logs) == ["vault_intake.extraction"]


async def test_x_engine_draft(haiku: FakeHaiku, logs: LogRecorder) -> None:
    assert await x_engine._chat("Draft ONE tweet.") == haiku.answer
    assert _fallback_purposes(logs) == ["x_engine.draft"]


async def test_video_engine_script(haiku: FakeHaiku, logs: LogRecorder) -> None:
    assert await video_engine._chat("Write the voiceover.") == haiku.answer
    assert _fallback_purposes(logs) == ["video_engine.script"]


async def test_validator_standards(
    monkeypatch: pytest.MonkeyPatch, logs: LogRecorder, tmp_path: Path
) -> None:
    follow_routing_mode(monkeypatch, "ollama")
    serve_fake_ollama(monkeypatch, FakeOllama(status=HTTPStatus.PAYMENT_REQUIRED))
    haiku = FakeHaiku(answer='{"violations": [], "summary": "ok"}')
    serve_fake_haiku(monkeypatch, haiku, tmp_path)

    violations, warnings = await ValidatorService()._validate_with_llm(
        action_type="create_endpoint",
        context="def foo(): pass",
        standards=[_citation()],
    )

    assert (violations, warnings) == ([], [])
    ((_prompt, options),) = haiku.calls
    assert options.system_prompt == VALIDATION_SYSTEM_PROMPT
    assert _fallback_purposes(logs) == ["validator.standards"]


async def test_validator_with_no_backend_raises_so_validate_uses_heuristics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    serve_fake_haiku(monkeypatch, FakeHaiku(answer=None), tmp_path)

    with pytest.raises(RuntimeError, match="no background-LLM backend answered"):
        await ValidatorService()._validate_with_llm(
            action_type="create_endpoint",
            context="def foo(): pass",
            standards=[_citation()],
        )


async def test_mentor_answer(haiku: FakeHaiku, logs: LogRecorder) -> None:
    answer = await MentorService()._synthesize_answer(
        question="How do I stop a flaky connect?",
        sources=[_citation()],
        conversation_context="",
        agent_profile=None,
        journal_context=[],
    )

    assert answer == haiku.answer
    assert _fallback_purposes(logs) == ["mentor.answer"]


async def test_index_ask(haiku: FakeHaiku, logs: LogRecorder) -> None:
    plugin = JournalsIndexPlugin.__new__(JournalsIndexPlugin)
    citations = [_citation()]

    answer, returned = await plugin._ask_llm("Answer from context.", citations)

    assert (answer, returned) == (haiku.answer, citations)
    assert _fallback_purposes(logs) == ["rag.index_ask.journals"]


async def test_eval_judge(haiku: FakeHaiku, logs: LogRecorder) -> None:
    assert await eval_runner._judge_chat("Score: <1-5>") == haiku.answer
    assert _fallback_purposes(logs) == ["eval.judge"]
