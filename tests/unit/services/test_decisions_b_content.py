"""Content-lane Tier B Decisions pilots (spec 7.1 rows B3, B4, B10, B11,
B18, B21, B22, B23, B24, B26): gates, thresholds, shadow/off semantics,
and the fail-open seams at each call site."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
import roboco.config as cfg
import structlog
from roboco.models.base import MessageType
from roboco.models.extraction import ExtractionContext
from roboco.services import telegram_bridge as bridge
from roboco.services.base import ValidationError
from roboco.services.decisions import pilots as pilots_mod
from roboco.services.decisions import pilots_content as pc
from roboco.services.decisions.client import DecisionsClient, DecisionsEndpoint
from roboco.services.decisions.pilots import PilotMode
from roboco.services.extraction import ExtractionService
from roboco.services.memory_distiller import LessonInput, MemoryDistiller
from roboco.services.proactive import ProactiveKnowledgeService
from roboco.services.prompter import PrompterService
from roboco.services.secretary import SecretaryService
from roboco.services.telegram_inbound import TelegramInboundEngine
from roboco.services.vault_intake_engine import VaultIntakeEngine
from roboco.services.video_engine import VideoEngine
from roboco.services.x_client import XMention
from roboco.services.x_engine import XEngine, changelog_highlights

_LAYA = DecisionsEndpoint(
    tier="laya",
    base_url="http://roboco-decisions:8100",
    model="convaiinnovations/laya",
    timeout_s=5.0,
)


def _payload(answers: dict) -> dict:
    return {
        "model": "convaiinnovations/laya",
        "answers": answers,
        "usage": {"input_tokens": 100, "output_tokens": 5, "cost": 0.0},
    }


def _arm(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: PilotMode = PilotMode.ON,
    payload: dict | None = None,
    fail: bool = False,
) -> None:
    """Arm the pilot stack: master flag on, chosen mode, laya endpoint, and
    a mocked transport returning ``payload`` (or failing)."""
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(pilots_mod, "pilot_mode", AsyncMock(return_value=mode))
    monkeypatch.setattr(pilots_mod, "resolve_endpoint", AsyncMock(return_value=_LAYA))

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=payload or {"answers": {}})

    client = DecisionsClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(pilots_mod, "get_decisions_client", lambda: client)


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", False)


def _bare(engine_cls: Any, **attrs: Any) -> Any:
    engine = engine_cls.__new__(engine_cls)
    engine.session = MagicMock()
    engine.session.flush = AsyncMock()
    engine.log = structlog.get_logger("test")
    for name, value in attrs.items():
        setattr(engine, name, value)
    return engine


# ---------------------------------------------------------------------------
# B3 intake_preroute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b3_off_is_none_without_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    mock = AsyncMock(return_value=(PilotMode.OFF, None))
    monkeypatch.setattr(pc, "decide_for_pilot", mock)
    assert await pc.intake_preroute(None, text="ship the thing", ref="c1") is None


@pytest.mark.asyncio
async def test_b3_shadow_acts_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "intake_chat", "confidence": 0.9}}
        ),
    )
    assert await pc.intake_preroute(None, text="ship the thing", ref="c1") is None


@pytest.mark.asyncio
async def test_b3_confident_routes_to_secretary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {
                "gate": {
                    "type": "choice",
                    "choice": "secretary_directive",
                    "confidence": 0.9,
                }
            }
        ),
    )
    verdict = await pc.intake_preroute(None, text="tell the devs to ship", ref="c1")
    assert verdict is pc.IntakeRoute.SECRETARY_DIRECTIVE


@pytest.mark.asyncio
async def test_b3_below_floor_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "noise", "confidence": 0.4}}
        ),
    )
    assert await pc.intake_preroute(None, text="hm ok", ref="c1") is None


@pytest.mark.asyncio
async def test_b3_telegram_off_keeps_silent_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _off(monkeypatch)
    engine = _bare(TelegramInboundEngine)
    client = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(bridge, "deliver_text", AsyncMock(return_value=None))
    start_secretary = AsyncMock()
    monkeypatch.setattr(bridge, "start_secretary", start_secretary)
    monkeypatch.setattr(pc, "intake_preroute", AsyncMock(return_value=None))
    monkeypatch.setattr(pc, "tg_freetext_gate", AsyncMock(return_value=False))
    await engine._route_free_text("1", "hello", client, object())
    client.send_message.assert_not_awaited()
    start_secretary.assert_not_awaited()


@pytest.mark.asyncio
async def test_b3_telegram_confident_intake_route_mirrors_newtask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(TelegramInboundEngine)
    client = SimpleNamespace(send_message=AsyncMock())
    creds = object()
    engine._cmd_newtask = AsyncMock()
    monkeypatch.setattr(bridge, "deliver_text", AsyncMock(return_value=None))
    monkeypatch.setattr(
        pc, "intake_preroute", AsyncMock(return_value=pc.IntakeRoute.INTAKE_CHAT)
    )
    await engine._route_free_text("1", "build a dashboard", client, creds)
    engine._cmd_newtask.assert_awaited_once_with(
        "1", "build a dashboard", creds, client
    )


@pytest.mark.asyncio
async def test_b3_telegram_confident_secretary_route_starts_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(TelegramInboundEngine)
    client = SimpleNamespace(send_message=AsyncMock())
    creds = object()
    monkeypatch.setattr(bridge, "deliver_text", AsyncMock(return_value=None))
    monkeypatch.setattr(
        pc,
        "intake_preroute",
        AsyncMock(return_value=pc.IntakeRoute.SECRETARY_DIRECTIVE),
    )
    start_secretary = AsyncMock(return_value="On it.")
    monkeypatch.setattr(bridge, "start_secretary", start_secretary)
    await engine._route_free_text("1", "status of the fleet", client, creds)
    start_secretary.assert_awaited_once_with("1", "status of the fleet", creds)
    client.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_b3_telegram_noise_suppresses_and_skips_b21(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(TelegramInboundEngine)
    client = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(bridge, "deliver_text", AsyncMock(return_value=None))
    monkeypatch.setattr(
        pc, "intake_preroute", AsyncMock(return_value=pc.IntakeRoute.NOISE)
    )
    gate = AsyncMock()
    monkeypatch.setattr(pc, "tg_freetext_gate", gate)
    await engine._route_free_text("1", "lol", client, object())
    client.send_message.assert_not_awaited()
    gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_b3_prompter_advisory_never_blocks_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _off(monkeypatch)
    svc: Any = PrompterService.__new__(PrompterService)
    svc.log = structlog.get_logger("test")
    svc._db = MagicMock()
    svc._db.commit = AsyncMock()
    svc._db.rollback = AsyncMock()
    monkeypatch.setattr(svc, "get_or_create_live_session", AsyncMock())
    intake_mock = AsyncMock(return_value=pc.IntakeRoute.NOISE)
    monkeypatch.setattr(pc, "intake_preroute", intake_mock)
    sid = uuid4().hex
    row = await svc.record_live_message(sid, "user", "hello there")
    assert row.content == "hello there"
    intake_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_b3_prompter_advisory_survives_pilot_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc: Any = PrompterService.__new__(PrompterService)
    svc.log = structlog.get_logger("test")
    svc._db = MagicMock()
    svc._db.commit = AsyncMock()
    svc._db.rollback = AsyncMock()
    monkeypatch.setattr(svc, "get_or_create_live_session", AsyncMock())

    async def boom(*a: object, **kw: object) -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(pc, "intake_preroute", boom)
    sid = uuid4().hex
    row = await svc.record_live_message(sid, "user", "hello there")
    assert row.role == "user"


# ---------------------------------------------------------------------------
# B4 x_mention_triage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b4_off_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert await pc.x_mention_triage(None, mention_id="m1", text="hi") is None


@pytest.mark.asyncio
async def test_b4_shadow_acts_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "spam", "confidence": 0.95}}
        ),
    )
    assert await pc.x_mention_triage(None, mention_id="m1", text="buy now") is None


@pytest.mark.asyncio
async def test_b4_confident_spam_suppresses(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "spam", "confidence": 0.95}}
        ),
    )
    assert (
        await pc.x_mention_triage(None, mention_id="m1", text="buy now")
        is pc.MentionTriage.SPAM
    )


@pytest.mark.asyncio
async def test_b4_bug_report_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "bug_report", "confidence": 0.9}}
        ),
    )
    assert (
        await pc.x_mention_triage(None, mention_id="m1", text="login is broken")
        is pc.MentionTriage.BUG_REPORT
    )


def _mention() -> XMention:
    return XMention(
        id="m1",
        author_id="a1",
        text="does roboco support gitea mirrors yet, asking for a friend",
        like_count=5,
        reply_count=2,
        retweet_count=1,
    )


@pytest.mark.asyncio
async def test_b4_wiring_spam_suppresses_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _bare(XEngine)
    monkeypatch.setattr(engine, "_already_seen", AsyncMock(return_value=False))
    monkeypatch.setattr(
        pc, "x_mention_triage", AsyncMock(return_value=pc.MentionTriage.SPAM)
    )
    assert await engine._skip_mention(_mention()) is True


@pytest.mark.asyncio
async def test_b4_wiring_question_proceeds_as_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(XEngine)
    monkeypatch.setattr(engine, "_already_seen", AsyncMock(return_value=False))
    monkeypatch.setattr(
        pc,
        "x_mention_triage",
        AsyncMock(return_value=pc.MentionTriage.REPLY_WORTHY_QUESTION),
    )
    assert await engine._skip_mention(_mention()) is False


@pytest.mark.asyncio
async def test_b4_wiring_pilot_failure_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(XEngine)
    monkeypatch.setattr(engine, "_already_seen", AsyncMock(return_value=False))

    async def boom(*a: object, **kw: object) -> bool:
        raise RuntimeError("down")

    monkeypatch.setattr(pc, "x_mention_triage", boom)
    assert await engine._skip_mention(_mention()) is False


# ---------------------------------------------------------------------------
# B10 segment_classify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b10_off_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert await pc.segment_classify(None, segments=["Decision: use X"]) == []


@pytest.mark.asyncio
async def test_b10_shadow_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload(
            {"seg_0": {"type": "choice", "choice": "decision", "confidence": 0.9}}
        ),
    )
    assert await pc.segment_classify(None, segments=["Decision: use X"]) == []


@pytest.mark.asyncio
async def test_b10_confident_verdicts_aligned(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {
                "seg_0": {"type": "choice", "choice": "decision", "confidence": 0.9},
                "seg_1": {"type": "choice", "choice": "dialogue", "confidence": 0.8},
            }
        ),
    )
    verdicts = await pc.segment_classify(
        None, segments=["Decision: use X", "Hey team, thoughts?"]
    )
    assert verdicts == [("decision", 0.9), ("dialogue", 0.8)]


@pytest.mark.asyncio
async def test_b10_below_floor_entry_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"seg_0": {"type": "choice", "choice": "decision", "confidence": 0.3}}
        ),
    )
    assert await pc.segment_classify(None, segments=["Decision: use X"]) == [None]


@pytest.mark.asyncio
async def test_b10_wiring_off_keeps_regex_without_pilot_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _off(monkeypatch)
    service = ExtractionService()
    mock = AsyncMock()
    monkeypatch.setattr(pc, "segment_classify", mock)
    ctx = ExtractionContext(
        content="Decision: use SQLite for the cache.\n\nDone: shipped.",
        agent_id=uuid4(),
        channel_id=uuid4(),
        session_id=uuid4(),
        group_id=uuid4(),
    )
    result = await service.extract(ctx)
    mock.assert_not_awaited()
    assert result.messages[0].type == MessageType.DECISION


@pytest.mark.asyncio
async def test_b10_wiring_confident_verdict_replaces_regex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    service = ExtractionService()
    monkeypatch.setattr(
        pc, "segment_classify", AsyncMock(return_value=[("blocker", 0.93)])
    )
    ctx = ExtractionContext(
        content="Decision: use SQLite for the cache.",
        agent_id=uuid4(),
        channel_id=uuid4(),
        session_id=uuid4(),
        group_id=uuid4(),
    )
    result = await service.extract(ctx)
    assert result.messages[0].type == MessageType.BLOCKER
    assert result.messages[0].confidence == 0.93


@pytest.mark.asyncio
async def test_b10_wiring_all_confident_skips_full_llm_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    service = ExtractionService()
    monkeypatch.setattr(
        pc,
        "segment_classify",
        AsyncMock(return_value=[("action", 0.9), ("blocker", 0.9)]),
    )
    called = AsyncMock()
    monkeypatch.setattr(service, "_call_anthropic_with_retry", called)
    ctx = ExtractionContext(
        content="Running the migration now.\n\nError: connection refused.",
        agent_id=uuid4(),
        channel_id=uuid4(),
        session_id=uuid4(),
        group_id=uuid4(),
    )
    result = await service.extract_with_llm(ctx)
    called.assert_not_awaited()
    assert result.messages[0].type == MessageType.ACTION


# ---------------------------------------------------------------------------
# B11 vault_prefilter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b11_off_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert await pc.vault_prefilter(None, note_path="n.md", body="body") is None


@pytest.mark.asyncio
async def test_b11_shadow_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload(
            {
                "gate": {"type": "noul", "noul": 0.05},
                "route": {
                    "type": "choice",
                    "choice": "reference_only",
                    "confidence": 0.9,
                },
            }
        ),
    )
    assert await pc.vault_prefilter(None, note_path="n.md", body="body") is None


@pytest.mark.asyncio
async def test_b11_confidently_not_worthy_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {
                "gate": {"type": "noul", "noul": 0.05},
                "route": {
                    "type": "choice",
                    "choice": "reference_only",
                    "confidence": 0.9,
                },
            }
        ),
    )
    verdict = await pc.vault_prefilter(None, note_path="n.md", body="grocery list")
    assert verdict is not None and verdict.skip is True


@pytest.mark.asyncio
async def test_b11_worthy_or_below_floor_runs_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {
                "gate": {"type": "noul", "noul": 0.6},
                "route": {
                    "type": "choice",
                    "choice": "intake_draft",
                    "confidence": 0.9,
                },
            }
        ),
    )
    verdict = await pc.vault_prefilter(None, note_path="n.md", body="ship X")
    assert verdict is not None and verdict.skip is False


@pytest.mark.asyncio
async def test_b11_wiring_confident_skip_avoids_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = _bare(VaultIntakeEngine)
    note = tmp_path / "note.md"
    note.write_text("#roboco just an idea, maybe", encoding="utf-8")
    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(engine, "_already_seen", AsyncMock(return_value=False))
    monkeypatch.setattr(
        pc,
        "vault_prefilter",
        AsyncMock(
            return_value=pc.VaultPrefilterVerdict(skip=True, route="reference_only")
        ),
    )
    engine._extract = AsyncMock(
        side_effect=AssertionError("extraction must be skipped")
    )
    assert await engine._process_note(note, uuid4()) is None
    engine._extract.assert_not_awaited()


# ---------------------------------------------------------------------------
# B18 secretary_nl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b18_kind_off_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert await pc.secretary_directive_kind(None, utterance="relay this") is None


@pytest.mark.asyncio
async def test_b18_kind_confident_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "relay_message", "confidence": 0.9}}
        ),
    )
    assert (
        await pc.secretary_directive_kind(None, utterance="relay this")
        == "relay_message"
    )


@pytest.mark.asyncio
async def test_b18_kind_below_floor_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "announce", "confidence": 0.3}}
        ),
    )
    assert await pc.secretary_directive_kind(None, utterance="whatever") is None


@pytest.mark.asyncio
async def test_b18_assignee_out_of_band_roster_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    mock = AsyncMock()
    monkeypatch.setattr(pc, "decide_for_pilot", mock)
    assert (
        await pc.secretary_assignee(None, utterance="the qa lead", candidate_slugs=[])
        is None
    )
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_b18_assignee_confident_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "fe-dev-2", "confidence": 0.9}}
        ),
    )
    assert (
        await pc.secretary_assignee(
            None,
            utterance="the second frontend dev",
            candidate_slugs=["be-dev-1", "fe-dev-2"],
        )
        == "fe-dev-2"
    )


@pytest.mark.asyncio
async def test_b18_wiring_kind_filled_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    svc = _bare(SecretaryService)
    monkeypatch.setattr(
        pc, "secretary_directive_kind", AsyncMock(return_value="relay_message")
    )
    from roboco.services.notification import NotificationService

    monkeypatch.setattr(NotificationService, "send_broadcast_notification", AsyncMock())
    row = await svc.submit_directive(None, {"text": "tell everyone hi"}, uuid4())
    assert row.kind == "relay_message"


@pytest.mark.asyncio
async def test_b18_wiring_kind_unset_unconfident_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    svc = _bare(SecretaryService)
    monkeypatch.setattr(pc, "secretary_directive_kind", AsyncMock(return_value=None))
    with pytest.raises(ValidationError):
        await svc.submit_directive(None, {"text": "mumble"}, uuid4())


@pytest.mark.asyncio
async def test_b18_wiring_explicit_kind_never_calls_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    svc = _bare(SecretaryService)
    mock = AsyncMock()
    monkeypatch.setattr(pc, "secretary_directive_kind", mock)
    from roboco.models.secretary import DirectiveKind
    from roboco.services.notification import NotificationService

    monkeypatch.setattr(NotificationService, "send_broadcast_notification", AsyncMock())
    row = await svc.submit_directive(
        DirectiveKind.RELAY_MESSAGE, {"text": "hello"}, uuid4()
    )
    assert row.kind == "relay_message"
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_b18_wiring_assignee_filled_on_slug_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    svc = _bare(SecretaryService)
    agent_id = uuid4()
    roster = [SimpleNamespace(slug="be-dev-1"), SimpleNamespace(slug="fe-dev-2")]
    result = MagicMock()
    result.scalars.return_value.all.return_value = [r.slug for r in roster]
    svc.session.execute = AsyncMock(return_value=result)

    import roboco.services.secretary as secretary_mod

    calls: list[str] = []

    async def fake_get_agent_by_slug(
        session: object, slug: str
    ) -> SimpleNamespace | None:
        calls.append(slug)
        if len(calls) == 1:
            return None
        return SimpleNamespace(id=agent_id)

    monkeypatch.setattr(secretary_mod, "get_agent_by_slug", fake_get_agent_by_slug)
    monkeypatch.setattr(pc, "secretary_assignee", AsyncMock(return_value="fe-dev-2"))
    assert await svc._resolve_assignee("the second frontend dev") == agent_id


@pytest.mark.asyncio
async def test_b18_wiring_assignee_off_raises_as_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _off(monkeypatch)
    svc = _bare(SecretaryService)
    mock = AsyncMock()
    monkeypatch.setattr(pc, "secretary_assignee", mock)

    import roboco.services.secretary as secretary_mod

    monkeypatch.setattr(
        secretary_mod, "get_agent_by_slug", AsyncMock(return_value=None)
    )
    with pytest.raises(ValidationError):
        await svc._resolve_assignee("nobody-knows")
    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# B21 tg_freetext_gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b21_off_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert await pc.tg_freetext_gate(None, chat_id="1", text="hello") is False


@pytest.mark.asyncio
async def test_b21_shadow_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload({"gate": {"type": "noul", "noul": 0.95}}),
    )
    assert await pc.tg_freetext_gate(None, chat_id="1", text="hello") is False


@pytest.mark.asyncio
async def test_b21_confident_deserves_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, payload=_payload({"gate": {"type": "noul", "noul": 0.95}}))
    assert await pc.tg_freetext_gate(None, chat_id="1", text="please fix X") is True


@pytest.mark.asyncio
async def test_b21_below_floor_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, payload=_payload({"gate": {"type": "noul", "noul": 0.5}}))
    assert await pc.tg_freetext_gate(None, chat_id="1", text="hey") is False


@pytest.mark.asyncio
async def test_b21_wiring_gate_adds_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _bare(TelegramInboundEngine)
    client = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(bridge, "deliver_text", AsyncMock(return_value=None))
    monkeypatch.setattr(pc, "intake_preroute", AsyncMock(return_value=None))
    monkeypatch.setattr(pc, "tg_freetext_gate", AsyncMock(return_value=True))
    from roboco.services.notification import NotificationService

    notify = AsyncMock()
    monkeypatch.setattr(NotificationService, "send_ack_notification", notify)
    await engine._route_free_text("1", "we should really fix X", client, object())
    client.send_message.assert_awaited_once()
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_b21_wiring_gate_below_floor_stays_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(TelegramInboundEngine)
    client = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(bridge, "deliver_text", AsyncMock(return_value=None))
    monkeypatch.setattr(pc, "intake_preroute", AsyncMock(return_value=None))
    monkeypatch.setattr(pc, "tg_freetext_gate", AsyncMock(return_value=False))
    await engine._route_free_text("1", "k", client, object())
    client.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# B22 memory_distill_gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b22_off_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert (
        await pc.memory_distill_gate(
            MagicMock(),
            title="t",
            acceptance_criteria=[],
            dev_notes=None,
            qa_notes=None,
            commit_messages=[],
        )
        is False
    )


@pytest.mark.asyncio
async def test_b22_shadow_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload({"gate": {"type": "noul", "noul": 0.05}}),
    )
    assert (
        await pc.memory_distill_gate(
            MagicMock(),
            title="t",
            acceptance_criteria=[],
            dev_notes=None,
            qa_notes=None,
            commit_messages=[],
        )
        is False
    )


@pytest.mark.asyncio
async def test_b22_confidently_not_worth_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, payload=_payload({"gate": {"type": "noul", "noul": 0.05}}))
    assert (
        await pc.memory_distill_gate(
            MagicMock(),
            title="bump deps",
            acceptance_criteria=[],
            dev_notes=None,
            qa_notes=None,
            commit_messages=["chore: bump"],
        )
        is True
    )


@pytest.mark.asyncio
async def test_b22_worth_or_below_floor_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch, payload=_payload({"gate": {"type": "noul", "noul": 0.6}}))
    assert (
        await pc.memory_distill_gate(
            MagicMock(),
            title="fix injection",
            acceptance_criteria=[],
            dev_notes="x",
            qa_notes=None,
            commit_messages=[],
        )
        is False
    )


@pytest.mark.asyncio
async def test_b22_wiring_gate_skips_llm_and_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(pc, "memory_distill_gate", AsyncMock(return_value=True))
    chat = AsyncMock()
    monkeypatch.setattr("roboco.services.memory_distiller._chat", chat)
    distiller = MemoryDistiller()
    assert await distiller.distill(LessonInput(title="t")) is None
    chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_b22_wiring_gate_open_distills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(pc, "memory_distill_gate", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "roboco.services.memory_distiller._chat",
        AsyncMock(return_value="Problem: p\nApproach: a\nGotcha: g"),
    )
    distiller = MemoryDistiller()
    lesson = await distiller.distill(LessonInput(title="t"))
    assert lesson is not None and lesson.startswith("Problem:")


# ---------------------------------------------------------------------------
# B23 changelog_highlights
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b23_off_keeps_first_bullet(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    highlights = ["Added: dashboard", "Fixed: login", "Security: patch"]
    assert (
        await pc.changelog_highlights_pick(
            None, version="1.0.0", product_name="RoboCo", highlights=highlights
        )
        == highlights
    )


@pytest.mark.asyncio
async def test_b23_shadow_keeps_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "2", "confidence": 0.9}}
        ),
    )
    highlights = ["Added: dashboard", "Fixed: login", "Security: patch"]
    assert (
        await pc.changelog_highlights_pick(
            None, version="1.0.0", product_name="RoboCo", highlights=highlights
        )
        == highlights
    )


@pytest.mark.asyncio
async def test_b23_confident_pick_leads(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "2", "confidence": 0.9}}
        ),
    )
    highlights = ["Added: dashboard", "Fixed: login", "Security: patch"]
    assert await pc.changelog_highlights_pick(
        None, version="1.0.0", product_name="RoboCo", highlights=highlights
    ) == ["Security: patch", "Added: dashboard", "Fixed: login"]


@pytest.mark.asyncio
async def test_b23_below_floor_and_single_entry_keep_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "1", "confidence": 0.2}}
        ),
    )
    highlights = ["Added: dashboard"]
    assert (
        await pc.changelog_highlights_pick(
            None, version="1.0.0", product_name="RoboCo", highlights=highlights
        )
        == highlights
    )


def test_b23_x_and_video_share_one_pilot() -> None:
    """Both call sites resolve to the same pilots_content function."""
    import inspect

    import roboco.services.video_engine as ve
    import roboco.services.x_engine as xe

    assert "changelog_highlights_pick" in inspect.getsource(
        xe.XEngine._decisions_top_highlight
    )
    assert "changelog_highlights_pick" in inspect.getsource(
        ve.VideoEngine._decisions_top_highlight
    )


@pytest.mark.asyncio
async def test_b23_x_wiring_pilot_reorders(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _bare(XEngine)
    monkeypatch.setattr(
        pc,
        "changelog_highlights_pick",
        AsyncMock(return_value=["Security: patch", "Added: dashboard", "Fixed: login"]),
    )
    out = await engine._decisions_top_highlight(
        "1.0.0", ["Added: dashboard", "Fixed: login", "Security: patch"], "RoboCo"
    )
    assert out[0] == "Security: patch"


@pytest.mark.asyncio
async def test_b23_video_wiring_pilot_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(VideoEngine)

    async def boom(*a: object, **kw: object) -> list[str]:
        raise RuntimeError("down")

    monkeypatch.setattr(pc, "changelog_highlights_pick", boom)
    highlights = ["Added: dashboard", "Fixed: login"]
    assert (
        await engine._decisions_top_highlight("1.0.0", highlights, "RoboCo")
        == highlights
    )


def test_b23_x_changelog_highlights_regex_unchanged() -> None:
    """The pure regex parser keeps its contract; the pilot only reorders."""
    body = "- **Dashboard (#1).**\n- **Login fix (#2).**"
    assert changelog_highlights(body) == ["Dashboard", "Login fix"]


# ---------------------------------------------------------------------------
# B24 proactive_domain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b24_off_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert (
        await pc.proactive_domain(
            None, task_type="bug", description="auth", keyword_domain="coding"
        )
        is None
    )


@pytest.mark.asyncio
async def test_b24_confident_overrides_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "security", "confidence": 0.9}}
        ),
    )
    assert (
        await pc.proactive_domain(
            None, task_type="chore", description="deps", keyword_domain="coding"
        )
        == "security"
    )


@pytest.mark.asyncio
async def test_b24_below_floor_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "workflow", "confidence": 0.4}}
        ),
    )
    assert (
        await pc.proactive_domain(
            None, task_type="bug", description="flow", keyword_domain="workflow"
        )
        is None
    )


@pytest.mark.asyncio
async def test_b24_wiring_off_keeps_keyword_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _off(monkeypatch)
    svc = ProactiveKnowledgeService()
    mock = AsyncMock()
    monkeypatch.setattr(pc, "proactive_domain", mock)
    assert await svc._decisions_domain("coding", "bug", "fix the thing") == "coding"
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_b24_wiring_confident_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    svc = ProactiveKnowledgeService()
    monkeypatch.setattr(pc, "proactive_domain", AsyncMock(return_value="security"))
    assert await svc._decisions_domain("coding", "bug", "harden auth") == "security"


# ---------------------------------------------------------------------------
# B26 decision_note_sufficiency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b26_off_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert (
        await pc.decision_note_sufficiency(None, kind="release", reason="ok") is False
    )


@pytest.mark.asyncio
async def test_b26_shadow_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload({"gate": {"type": "score", "score": 2.0, "confidence": 0.95}}),
    )
    assert (
        await pc.decision_note_sufficiency(None, kind="release", reason="ok") is False
    )


@pytest.mark.asyncio
async def test_b26_substantive_score_2_relaxes(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload({"gate": {"type": "score", "score": 2.0, "confidence": 0.95}}),
    )
    assert await pc.decision_note_sufficiency(None, kind="release", reason="ok") is True


@pytest.mark.asyncio
async def test_b26_low_score_keeps_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload({"gate": {"type": "score", "score": 1.0, "confidence": 0.95}}),
    )
    assert (
        await pc.decision_note_sufficiency(None, kind="release", reason="ok") is False
    )


@pytest.mark.asyncio
async def test_b26_wiring_substantive_note_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(TelegramInboundEngine)
    monkeypatch.setattr(pc, "decision_note_sufficiency", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_resolve_task", AsyncMock(return_value=None))
    ok, message = await engine._dispatch_reject("release", "abcd1234", "", "ok")
    assert ok is False
    assert message == "No such release item: abcd1234"


@pytest.mark.asyncio
async def test_b26_wiring_unsubstantive_note_keeps_char_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(TelegramInboundEngine)
    monkeypatch.setattr(pc, "decision_note_sufficiency", AsyncMock(return_value=False))
    ok, message = await engine._dispatch_reject("release", "abcd1234", "", "ok")
    assert ok is False
    assert "Rejection not recorded" in message


@pytest.mark.asyncio
async def test_b26_wiring_long_note_never_consults_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(TelegramInboundEngine)
    mock = AsyncMock()
    monkeypatch.setattr(pc, "decision_note_sufficiency", mock)
    monkeypatch.setattr(engine, "_resolve_task", AsyncMock(return_value=None))
    reason = "this release misses the changelog entry for the migration"
    _ok, message = await engine._dispatch_reject("release", "abcd1234", "", reason)
    mock.assert_not_awaited()
    assert message == "No such release item: abcd1234"
