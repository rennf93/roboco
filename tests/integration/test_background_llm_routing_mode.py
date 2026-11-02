"""The background LLM client follows the routing mode in the real routing table.

``read_routing_mode`` opens its own session through ``get_db_context`` (most
callers hold none), so these tests point the app's settings at the throwaway
test database and seed ``model_assignments`` through the routing service.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from roboco.db.tables import ProviderConfigTable
from roboco.models.base import AssignmentScope, ModelProvider
from roboco.services import background_llm
from roboco.services.background_llm import (
    BackgroundPrompt,
    generate_background_reply,
    read_routing_mode,
)
from roboco.services.llm import get_model_routing_service

from tests.unit.services.background_llm_fakes import (
    FakeHaiku,
    FakeOllama,
    serve_fake_haiku,
    serve_fake_ollama,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.usefixtures("app_settings_on_test_db")

_PROMPT = BackgroundPrompt(
    purpose="test.routing_mode",
    user_prompt="ping",
    max_tokens=5,
    timeout_seconds=5.0,
)


async def _route_everything_to_ollama(session: AsyncSession) -> None:
    session.add(
        ProviderConfigTable(
            name="ollama-test",
            type=ModelProvider.OLLAMA_CLOUD,
            enabled=True,
            base_url="https://ollama.example.com",
        )
    )
    await session.flush()
    await get_model_routing_service(session).upsert_assignment(
        scope=AssignmentScope.GLOBAL,
        scope_value=None,
        model_name="glm-5.3-flash:cloud",
    )
    await session.commit()


@pytest.mark.usefixtures("db_session")
async def test_an_empty_routing_table_reads_as_anthropic() -> None:
    assert await read_routing_mode() == "anthropic"


async def test_a_global_ollama_assignment_reads_as_ollama(
    db_session: AsyncSession,
) -> None:
    await _route_everything_to_ollama(db_session)

    assert await read_routing_mode() == "ollama"


@pytest.mark.usefixtures("db_session")
async def test_claude_as_main_driver_never_calls_ollama(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(background_llm, "read_routing_mode", read_routing_mode)
    ollama = FakeOllama()
    serve_fake_ollama(monkeypatch, ollama)
    serve_fake_haiku(monkeypatch, FakeHaiku(), tmp_path)

    reply = await generate_background_reply(_PROMPT)

    assert reply.backend == "claude"
    assert ollama.requests == []


async def test_ollama_as_main_driver_tries_ollama_first(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _route_everything_to_ollama(db_session)
    monkeypatch.setattr(background_llm, "read_routing_mode", read_routing_mode)
    ollama = FakeOllama(status=HTTPStatus.PAYMENT_REQUIRED)
    serve_fake_ollama(monkeypatch, ollama)
    serve_fake_haiku(monkeypatch, FakeHaiku(), tmp_path)

    reply = await generate_background_reply(_PROMPT)

    assert len(ollama.requests) == 1
    assert reply.backend == "claude"
