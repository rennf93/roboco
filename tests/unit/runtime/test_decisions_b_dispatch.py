"""Wiring tests for the Tier B dispatch pilots (spec 7.1 rows B14, B25,
B30, B32, B33, B35, B36, B37, B38, B40, B42, B44). Focus: off = no-op,
shadow = acts as off, a confident verdict applies, and below-floor falls
back to exactly today's behavior. Engines are instantiated bare via
``__new__`` and the pilot entry points are monkeypatched, so no DB or
Decisions endpoint is ever touched."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from roboco.foundation.policy.content import markers as _markers
from roboco.runtime.engines.dispatch_breaker import DispatchBreakerEngine
from roboco.runtime.engines.dispatch_claim import DispatchClaimEngine
from roboco.runtime.engines.dispatch_prompts import DispatchPromptsEngine
from roboco.runtime.engines.dispatch_work import DispatchWorkEngine
from roboco.runtime.engines.spawn_exit import SpawnExitEngine
from roboco.runtime.engines.sweeps import SweepsEngine
from roboco.services.decisions import pilots_dispatch
from roboco.services.decisions.schemas import DecisionAnswer, DecisionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bare(engine_cls: Any) -> Any:
    """Instantiate an engine mixin without __init__ (no DB needed)."""
    engine = engine_cls.__new__(engine_cls)
    engine.log = structlog.get_logger("test")
    return engine


def _result(answers: dict[str, dict]) -> DecisionResult:
    return DecisionResult(
        answers={
            key: DecisionAnswer(
                key=key,
                type=body.get("type", "noul"),
                noul=body.get("noul"),
                choice=body.get("choice"),
                score=body.get("score"),
                confidence=body.get("confidence"),
            )
            for key, body in answers.items()
        },
        tier="laya",
        session_id="test",
    )


def _patch_decide(
    monkeypatch: pytest.MonkeyPatch,
    mode: pilots_dispatch.PilotMode,
    result: DecisionResult | None = None,
) -> dict[str, Any]:
    """Monkeypatch pilots_dispatch.decide_for_pilot (the single seam)."""
    seen: dict[str, Any] = {}

    async def fake_decide(
        session: object,
        pilot: object,
        state: object,
        questions: object,
        session_id: object,
    ) -> tuple[pilots_dispatch.PilotMode, DecisionResult | None]:
        seen["pilot"] = pilot
        seen["state"] = state
        seen["questions"] = questions
        return mode, result

    monkeypatch.setattr(pilots_dispatch, "decide_for_pilot", fake_decide)
    return seen


def _flag_on(monkeypatch: pytest.MonkeyPatch, enabled: bool = True) -> None:
    import roboco.config as cfg

    monkeypatch.setattr(cfg.settings, "decisions_enabled", enabled)


# ---------------------------------------------------------------------------
# B14 external_pr_triage (FAIL-CLOSED injection screen)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b14_off_flags_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_on(monkeypatch, False)
    _patch_decide(monkeypatch, pilots_dispatch.PilotMode.OFF)
    priority, flagged = await pilots_dispatch.external_pr_triage(
        MagicMock(), project_slug="p", pr={"title": "x"}, review_kind="external_pr"
    )
    assert priority is None
    assert flagged is False


@pytest.mark.asyncio
async def test_b14_confident_priority_and_flag_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result(
            {
                "priority": {"type": "score", "score": 2, "confidence": 0.9},
                "injection": {"type": "noul", "noul": 0.9, "confidence": 0.9},
            }
        ),
    )
    priority, flagged = await pilots_dispatch.external_pr_triage(
        MagicMock(), project_slug="p", pr={"title": "x"}, review_kind="external_pr"
    )
    assert priority == 2
    assert flagged is True


@pytest.mark.asyncio
async def test_b14_below_confidence_counts_as_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAIL-CLOSED: a below-floor-confidence injection answer is a flag."""
    _flag_on(monkeypatch)
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result(
            {
                "injection": {"type": "noul", "noul": 0.1, "confidence": 0.4},
            }
        ),
    )
    _priority, flagged = await pilots_dispatch.external_pr_triage(
        MagicMock(), project_slug="p", pr={"title": "x"}, review_kind="external_pr"
    )
    assert flagged is True


@pytest.mark.asyncio
async def test_b14_unreachable_classifier_flags_while_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    _patch_decide(monkeypatch, pilots_dispatch.PilotMode.ON, None)
    _priority, flagged = await pilots_dispatch.external_pr_triage(
        MagicMock(), project_slug="p", pr={"title": "x"}, review_kind="external_pr"
    )
    assert flagged is True


@pytest.mark.asyncio
async def test_b14_shadow_flags_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_on(monkeypatch)
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.SHADOW,
        _result(
            {
                "injection": {"type": "noul", "noul": 0.95, "confidence": 0.95},
            }
        ),
    )
    priority, flagged = await pilots_dispatch.external_pr_triage(
        MagicMock(), project_slug="p", pr={"title": "x"}, review_kind="external_pr"
    )
    assert (priority, flagged) == (None, False)


@pytest.mark.asyncio
async def test_b14_engine_flags_marker_and_top_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(SweepsEngine)

    async def fake_triage(session: object, **kwargs: object) -> tuple[int | None, bool]:
        return 1, True

    monkeypatch.setattr(pilots_dispatch, "external_pr_triage", fake_triage)
    created = SimpleNamespace(
        priority=2, orchestration_markers=None, source="external_pr"
    )

    @asynccontextmanager
    async def fake_nested() -> AsyncIterator[None]:
        yield

    task_service = SimpleNamespace(
        session=SimpleNamespace(flush=AsyncMock(), begin_nested=fake_nested),
    )
    await engine._decisions_external_pr_triage(
        task_service, SimpleNamespace(slug="p"), {"number": 1}, created
    )
    assert created.priority == 3
    assert _markers.get_marker(created, "external_pr_injection_flag") is not None
    task_service.session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_b14_engine_internal_pr_not_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-closed injection screen runs on external/fork PRs only: an
    org-internal PR keeps its structural classification (its own flag bit
    is logged but the marker and priority-3 stamp never fire)."""
    _flag_on(monkeypatch)
    engine = _bare(SweepsEngine)

    async def fake_triage(session: object, **kwargs: object) -> tuple[int | None, bool]:
        return None, True

    monkeypatch.setattr(pilots_dispatch, "external_pr_triage", fake_triage)
    created = SimpleNamespace(
        priority=2, orchestration_markers=None, source="internal_pr"
    )

    @asynccontextmanager
    async def fake_nested() -> AsyncIterator[None]:
        yield

    task_service = SimpleNamespace(
        session=SimpleNamespace(flush=AsyncMock(), begin_nested=fake_nested),
    )
    await engine._decisions_external_pr_triage(
        task_service, SimpleNamespace(slug="p"), {"number": 1}, created
    )
    assert created.priority == 2
    assert _markers.get_marker(created, "external_pr_injection_flag") is None
    task_service.session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_b14_engine_off_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_on(monkeypatch, False)
    engine = _bare(SweepsEngine)
    mock = AsyncMock()
    monkeypatch.setattr(pilots_dispatch, "external_pr_triage", mock)
    created = SimpleNamespace(
        priority=2, orchestration_markers=None, source="external_pr"
    )
    await engine._decisions_external_pr_triage(
        SimpleNamespace(session=MagicMock()), SimpleNamespace(slug="p"), {}, created
    )
    mock.assert_not_awaited()
    assert created.priority == 2


@pytest.mark.asyncio
async def test_b14_queue_order_falls_back_to_fetch_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch, False)
    engine = _bare(DispatchWorkEngine)
    low = {"id": "low", "priority": 0}
    high = {"id": "high", "priority": 3}
    ordered, depths = await engine._decisions_order_qa_queue([low, high])
    assert [t["id"] for t in ordered] == ["low", "high"]
    assert depths == {}


@pytest.mark.asyncio
async def test_b14_review_dispatch_sorts_by_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _bare(DispatchWorkEngine)
    engine._is_agent_active = lambda _slug: False
    engine._fetch_tasks = AsyncMock(
        return_value=[
            {"id": "low", "source": "external_pr", "priority": 0, "assigned_to": None},
            {"id": "high", "source": "external_pr", "priority": 3, "assigned_to": None},
        ]
    )
    engine._is_task_handled_this_tick = lambda _tid: False
    engine._pm_respawn_should_gate = AsyncMock(return_value=False)
    engine._build_pr_review_prompt = lambda _t: "prompt"
    engine._task_git_context = lambda _t: None
    spawn = AsyncMock()
    engine.spawn_agent = spawn
    await engine._dispatch_pr_review_work(MagicMock())
    assert spawn.await_args is not None
    assert spawn.await_args.kwargs["task_id"] == "high"


# ---------------------------------------------------------------------------
# B25 idle_reaping (extend-only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b25_pilot_off_and_shadow_act_as_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_decide(monkeypatch, pilots_dispatch.PilotMode.OFF)
    assert (
        await pilots_dispatch.idle_abandonment_verdicts(
            MagicMock(), sessions=[{"session_id": "s", "agent_id": "a"}]
        )
        is None
    )
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.SHADOW,
        _result({"sess_0": {"type": "noul", "noul": 0.95, "confidence": 0.95}}),
    )
    assert (
        await pilots_dispatch.idle_abandonment_verdicts(
            MagicMock(), sessions=[{"session_id": "s", "agent_id": "a"}]
        )
        is None
    )


@pytest.mark.asyncio
async def test_b25_confident_still_active_extends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result({"sess_0": {"type": "noul", "noul": 0.85, "confidence": 0.9}}),
    )
    verdicts = await pilots_dispatch.idle_abandonment_verdicts(
        MagicMock(), sessions=[{"session_id": "s", "agent_id": "a"}]
    )
    assert verdicts == [True]


@pytest.mark.asyncio
async def test_b25_below_floor_reaps_as_today(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result({"sess_0": {"type": "noul", "noul": 0.3, "confidence": 0.9}}),
    )
    verdicts = await pilots_dispatch.idle_abandonment_verdicts(
        MagicMock(), sessions=[{"session_id": "s", "agent_id": "a"}]
    )
    assert verdicts == [False]


@pytest.mark.asyncio
async def test_b25_engine_spares_only_confident_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(SweepsEngine)

    due = [("a", "intake-1"), ("b", "intake-1"), ("c", "intake-1")]

    fake_registry = MagicMock()
    # c is past the extension window (2x); a and b are in the band.
    fake_registry.idle_session_ids = lambda threshold: (
        due if threshold <= 1 else [("c", "intake-1")]
    )

    import roboco.services.prompter_live as pl

    monkeypatch.setattr(pl, "get_live_registry", lambda: fake_registry)

    async def fake_verdicts(
        session: object, *, sessions: list[dict[str, str]]
    ) -> list[bool]:
        ids = [s["session_id"] for s in sessions]
        return [sid == "a" for sid in ids]

    monkeypatch.setattr(pilots_dispatch, "idle_abandonment_verdicts", fake_verdicts)

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)

    spared = await engine._decisions_spare_not_abandoned(due, 1.0)
    assert [sid for sid, _aid in spared] == ["b", "c"]


@pytest.mark.asyncio
async def test_b25_engine_failure_reaps_as_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(SweepsEngine)
    due = [("a", "intake-1")]

    fake_registry = MagicMock()
    fake_registry.idle_session_ids = lambda threshold: due if threshold <= 1 else []
    import roboco.services.prompter_live as pl

    monkeypatch.setattr(pl, "get_live_registry", lambda: fake_registry)

    async def boom(*a: object, **kw: object) -> list[bool]:
        raise RuntimeError("down")

    monkeypatch.setattr(pilots_dispatch, "idle_abandonment_verdicts", boom)

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)
    assert await engine._decisions_spare_not_abandoned(due, 1.0) == due


# ---------------------------------------------------------------------------
# B30 context_pruning (drop-only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b30_off_and_shadow_inject_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = ["a", "b", "c"]
    _patch_decide(monkeypatch, pilots_dispatch.PilotMode.OFF)
    assert (
        await pilots_dispatch.context_relevance_verdicts(
            MagicMock(), task_id="t", entries=entries, workflow_state="EXECUTING"
        )
        is None
    )
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.SHADOW,
        _result(
            {
                f"entry_{i}": {"type": "noul", "noul": 0.1, "confidence": 0.95}
                for i in range(3)
            }
        ),
    )
    assert (
        await pilots_dispatch.context_relevance_verdicts(
            MagicMock(), task_id="t", entries=entries, workflow_state="EXECUTING"
        )
        is None
    )


@pytest.mark.asyncio
async def test_b30_confident_irrelevant_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result(
            {
                "entry_0": {"type": "noul", "noul": 0.9, "confidence": 0.9},
                "entry_1": {"type": "noul", "noul": 0.1, "confidence": 0.9},
                "entry_2": {"type": "noul", "noul": 0.9, "confidence": 0.9},
            }
        ),
    )
    keeps = await pilots_dispatch.context_relevance_verdicts(
        MagicMock(), task_id="t", entries=["a", "b", "c"], workflow_state="EXECUTING"
    )
    assert keeps == [True, False, True]


@pytest.mark.asyncio
async def test_b30_below_floor_keeps_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result({"entry_0": {"type": "noul", "noul": 0.1, "confidence": 0.5}}),
    )
    keeps = await pilots_dispatch.context_relevance_verdicts(
        MagicMock(), task_id="t", entries=["a"], workflow_state="EXECUTING"
    )
    assert keeps == [True]


@pytest.mark.asyncio
async def test_b30_engine_never_offers_protected_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(DispatchPromptsEngine)
    engine._description_body = staticmethod(lambda text, **kw: text)
    description = (
        "Some rambling background about the weather.\n\n"
        "Acceptance criteria: the widget must render.\n\n"
        "More rambling notes about unrelated refactors.\n\n"
        "Even more filler paragraphs to reach the entry minimum.\n\n"
        "And one more filler paragraph after that.\n\n"
        "A final filler paragraph to pad the count out."
    )
    seen: dict = {}

    async def fake_verdicts(
        session: object,
        *,
        task_id: object,
        entries: list[str],
        workflow_state: object,
    ) -> list[bool]:
        seen["entries"] = list(entries)
        return [True] * len(entries)

    monkeypatch.setattr(pilots_dispatch, "context_relevance_verdicts", fake_verdicts)
    rendered = await engine._decisions_pruned_description(
        description,
        task_id="t",
        workflow_state="EXECUTING",
        fallback="FALLBACK",
    )
    # The protected AC entry was never offered for dropping...
    assert not any("Acceptance criteria" in e for e in seen["entries"])
    # ...and nothing was dropped, so the fallback (today's rendering) stands.
    assert rendered == "FALLBACK"


@pytest.mark.asyncio
async def test_b30_engine_drops_only_offered_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(DispatchPromptsEngine)
    engine._description_body = staticmethod(lambda text, **kw: text)
    description = (
        "Filler paragraph one about nothing.\n\n"
        "Acceptance criteria: the widget must render.\n\n"
        "Filler paragraph two about less.\n\n"
        "Filler paragraph three about trivia.\n\n"
        "Filler paragraph four about noise.\n\n"
        "Filler paragraph five to finish."
    )
    seen: dict = {}

    async def fake_verdicts(
        session: object,
        *,
        task_id: object,
        entries: list[str],
        workflow_state: object,
    ) -> list[bool]:
        seen["entries"] = list(entries)
        # Confidently irrelevant: drop every offered (unprotected) entry.
        return [False] * len(entries)

    monkeypatch.setattr(pilots_dispatch, "context_relevance_verdicts", fake_verdicts)
    rendered = await engine._decisions_pruned_description(
        description,
        task_id="t",
        workflow_state="EXECUTING",
        fallback="FALLBACK",
    )
    assert "Filler paragraph one" not in rendered
    # The protected AC entry ALWAYS survives the prune.
    assert "Acceptance criteria" in rendered


@pytest.mark.asyncio
async def test_b30_engine_short_description_bypasses_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(DispatchPromptsEngine)
    mock = AsyncMock()
    monkeypatch.setattr(pilots_dispatch, "context_relevance_verdicts", mock)
    rendered = await engine._decisions_pruned_description(
        "only one paragraph",
        task_id="t",
        workflow_state="EXECUTING",
        fallback="FALLBACK",
    )
    assert rendered == "FALLBACK"
    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# B32 budget_wrapup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b32_off_shadow_and_below_floor_push_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mode, result in [
        (pilots_dispatch.PilotMode.OFF, None),
        (pilots_dispatch.PilotMode.SHADOW, None),
        (
            pilots_dispatch.PilotMode.ON,
            _result(
                {
                    "gate": {
                        "type": "choice",
                        "choice": "wrap-up-and-submit",
                        "confidence": 0.5,
                    }
                }
            ),
        ),
    ]:
        _patch_decide(monkeypatch, mode, result)
        choice = await pilots_dispatch.budget_wrapup_choice(
            MagicMock(),
            agent_id="be-dev-1",
            task_id="t",
            total_calls=120,
            halt_threshold=150,
            task_state={},
        )
        assert choice is pilots_dispatch.BudgetWrapup.PUSH_TO_FINISH


@pytest.mark.asyncio
async def test_b32_confident_wrap_up_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result(
            {
                "gate": {
                    "type": "choice",
                    "choice": "wrap-up-and-submit",
                    "confidence": 0.9,
                }
            }
        ),
    )
    choice = await pilots_dispatch.budget_wrapup_choice(
        MagicMock(),
        agent_id="a",
        task_id="t",
        total_calls=120,
        halt_threshold=150,
        task_state={},
    )
    assert choice is pilots_dispatch.BudgetWrapup.WRAP_UP_AND_SUBMIT


@pytest.mark.asyncio
async def test_b32_engine_abort_clean_stops_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(SweepsEngine)

    async def fake_choice(
        session: object, **kwargs: object
    ) -> pilots_dispatch.BudgetWrapup:
        return pilots_dispatch.BudgetWrapup.ABORT_CLEAN

    monkeypatch.setattr(pilots_dispatch, "budget_wrapup_choice", fake_choice)

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)
    stop = AsyncMock()
    engine.stop_agent = stop
    instance = SimpleNamespace(current_task_id=None)
    await engine._decisions_budget_wrapup_directive(
        "be-dev-1", instance, {"total": 120, "halt_threshold": 150, "warn": True}
    )
    stop.assert_awaited_once()
    assert stop.await_args is not None
    assert stop.await_args.kwargs["stop_reason"] == "budget_wrapup_abort"


@pytest.mark.asyncio
async def test_b32_engine_off_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_on(monkeypatch, False)
    engine = _bare(SweepsEngine)
    mock = AsyncMock()
    monkeypatch.setattr(pilots_dispatch, "budget_wrapup_choice", mock)
    await engine._decisions_budget_wrapup_directive(
        "be-dev-1", SimpleNamespace(current_task_id=None), {"warn": True}
    )
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_b32_engine_wrapup_fires_once_per_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(SweepsEngine)

    calls: list[dict[str, Any]] = []

    async def fake_choice(
        session: object, **kwargs: object
    ) -> pilots_dispatch.BudgetWrapup:
        calls.append(kwargs)
        return pilots_dispatch.BudgetWrapup.WRAP_UP_AND_HANDOFF_NOTE

    monkeypatch.setattr(pilots_dispatch, "budget_wrapup_choice", fake_choice)

    task_id = "00000000-0000-0000-0000-000000000001"

    class _TaskService:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, _tid: object) -> SimpleNamespace:
            return SimpleNamespace(
                commits=[],
                pr_created=False,
                status="in_progress",
                orchestration_markers=None,
            )

    import roboco.services.task as task_mod

    monkeypatch.setattr(task_mod, "get_task_service", lambda db: _TaskService(db))

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)
    monkeypatch.setattr(
        SweepsEngine,
        "_notify_budget_wrapup_directive",
        AsyncMock(return_value=None),
    )
    instance = SimpleNamespace(current_task_id=task_id)
    data = {"total": 120, "halt_threshold": 150, "warn": True}
    await engine._decisions_budget_wrapup_directive("be-dev-1", instance, data)
    await engine._decisions_budget_wrapup_directive("be-dev-1", instance, data)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# B33 delta_brief (one classifier, three trigger points)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b33_off_shadow_below_floor_render_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mode, result in [
        (pilots_dispatch.PilotMode.OFF, None),
        (pilots_dispatch.PilotMode.SHADOW, None),
        (
            pilots_dispatch.PilotMode.ON,
            _result(
                {
                    "gate": {
                        "type": "choice",
                        "choice": "delta-brief",
                        "confidence": 0.5,
                    },
                    "depth": {"type": "score", "score": 2, "confidence": 0.9},
                }
            ),
        ),
    ]:
        _patch_decide(monkeypatch, mode, result)
        verdict = await pilots_dispatch.delta_brief(
            MagicMock(),
            task_id="t",
            trigger="revision_respawn",
            task_state={"commit_count": 3},
        )
        assert verdict is None


@pytest.mark.asyncio
async def test_b33_confident_delta_applies_with_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result(
            {
                "gate": {"type": "choice", "choice": "delta-brief", "confidence": 0.9},
                "depth": {"type": "score", "score": 3, "confidence": 0.9},
            }
        ),
    )
    verdict = await pilots_dispatch.delta_brief(
        MagicMock(),
        task_id="t",
        trigger="revision_respawn",
        task_state={"commit_count": 3},
    )
    assert verdict is not None
    mode, depth = verdict
    assert mode is pilots_dispatch.DeltaBriefMode.DELTA_BRIEF
    assert depth == 3
    assert seen["state"]["trigger"] == "revision_respawn"


@pytest.mark.asyncio
async def test_b33_engine_off_renders_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_on(monkeypatch, False)
    engine = _bare(DispatchPromptsEngine)
    mock = AsyncMock()
    monkeypatch.setattr(pilots_dispatch, "delta_brief", mock)
    assert await engine._delta_brief_block({"id": "t"}) == ""
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_b33_engine_delta_renders_prior_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(DispatchPromptsEngine)
    engine._parse_iso_dt = staticmethod(lambda _v: None)

    async def fake_delta(
        session: object, *, task_id: object, trigger: object, task_state: object
    ) -> tuple[pilots_dispatch.DeltaBriefMode, int | None]:
        return pilots_dispatch.DeltaBriefMode.DELTA_BRIEF, 2

    monkeypatch.setattr(pilots_dispatch, "delta_brief", fake_delta)
    block = await engine._delta_brief_block(
        {
            "id": "t",
            "status": "needs_revision",
            "commits": [{"hash": "abc1234abcd", "message": "add widget"}],
        }
    )
    assert "WHERE YOUR PRIOR ATTEMPT LEFT OFF" in block
    assert "add widget" in block


@pytest.mark.asyncio
async def test_b33_engine_fresh_start_renders_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(DispatchPromptsEngine)
    engine._parse_iso_dt = staticmethod(lambda _v: None)

    async def fake_delta(
        session: object, **kwargs: object
    ) -> tuple[pilots_dispatch.DeltaBriefMode, int | None]:
        return pilots_dispatch.DeltaBriefMode.FRESH_START_BRIEF, 0

    monkeypatch.setattr(pilots_dispatch, "delta_brief", fake_delta)
    assert await engine._delta_brief_block({"id": "t", "status": "claimed"}) == ""


# ---------------------------------------------------------------------------
# B35 review_queue_priority (order + depth, never verdicts)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b35_off_and_shadow_keep_fetch_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = [{"id": "1"}, {"id": "2"}]
    _patch_decide(monkeypatch, pilots_dispatch.PilotMode.OFF)
    assert await pilots_dispatch.review_queue_verdicts(MagicMock(), tasks=tasks) is None
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.SHADOW,
        _result({"t0_priority": {"type": "score", "score": 2, "confidence": 0.9}}),
    )
    assert await pilots_dispatch.review_queue_verdicts(MagicMock(), tasks=tasks) is None


@pytest.mark.asyncio
async def test_b35_confident_scores_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result(
            {
                "t0_priority": {"type": "score", "score": 2, "confidence": 0.9},
                "t0_depth": {"type": "score", "score": 3, "confidence": 0.9},
                "t1_priority": {"type": "score", "score": 0, "confidence": 0.9},
                "t1_depth": {"type": "score", "score": 5, "confidence": 0.9},
            }
        ),
    )
    verdicts = await pilots_dispatch.review_queue_verdicts(
        MagicMock(), tasks=[{"id": "1"}, {"id": "2"}]
    )
    assert verdicts == [(2, 3), (0, 3)]  # out-of-band depth clamped to 3


@pytest.mark.asyncio
async def test_b35_engine_orders_and_injects_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(DispatchWorkEngine)
    low = {"id": "low", "team": "backend"}
    high = {"id": "high", "team": "backend"}

    async def fake_verdicts(
        session: object, *, tasks: list[dict[str, str]]
    ) -> list[tuple[int | None, int | None]]:
        return [(0, 1), (2, 3)]

    monkeypatch.setattr(pilots_dispatch, "review_queue_verdicts", fake_verdicts)

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)
    ordered, depths = await engine._decisions_order_qa_queue([low, high])
    assert [t["id"] for t in ordered] == ["high", "low"]
    assert depths["high"] == 3
    directive = engine._decisions_qa_depth_directive(high, depths)
    assert "forensic" in directive
    assert engine._decisions_qa_depth_directive(low, depths) == ""


@pytest.mark.asyncio
async def test_b35_engine_keeps_tasks_beyond_the_scored_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pilot scores a capped prefix; tasks beyond it are appended
    UNSCORED, never dropped from this tick's dispatch (the regression
    made tasks 13+ invisible to the dispatcher while the same head of
    the queue was re-scored every tick)."""
    _flag_on(monkeypatch)
    engine = _bare(DispatchWorkEngine)
    candidates = [{"id": f"t{i}", "team": "backend"} for i in range(15)]

    async def fake_verdicts(
        session: object, *, tasks: list[dict[str, str]]
    ) -> list[tuple[int | None, int | None]]:
        assert len(tasks) == 15  # the caller passes the whole queue
        return [(2, None)] * 12  # the pilot only scored the first 12

    monkeypatch.setattr(pilots_dispatch, "review_queue_verdicts", fake_verdicts)

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)
    ordered, _depths = await engine._decisions_order_qa_queue(candidates)
    assert len(ordered) == len(candidates)
    assert {t["id"] for t in ordered} == {t["id"] for t in candidates}
    # The scored head (priority 2) sorts before the unscored tail.
    assert {t["id"] for t in ordered[:12]} == {f"t{i}" for i in range(12)}
    assert {t["id"] for t in ordered[12:]} == {"t12", "t13", "t14"}


# ---------------------------------------------------------------------------
# B36 board_evidence_skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b36_off_shadow_and_below_floor_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mode, result in [
        (pilots_dispatch.PilotMode.OFF, None),
        (pilots_dispatch.PilotMode.SHADOW, None),
        (
            pilots_dispatch.PilotMode.ON,
            _result({"gate": {"type": "noul", "noul": 0.5}}),
        ),
    ]:
        _patch_decide(monkeypatch, mode, result)
        assert (
            await pilots_dispatch.board_evidence_skip(
                MagicMock(), program="pest_control", evidence_context="lots of noise"
            )
            is False
        )


@pytest.mark.asyncio
async def test_b36_confident_no_op_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result({"gate": {"type": "noul", "noul": 0.95, "confidence": 0.95}}),
    )
    assert (
        await pilots_dispatch.board_evidence_skip(
            MagicMock(), program="scales", evidence_context="empty dump"
        )
        is True
    )


@pytest.mark.asyncio
async def test_b36_empty_evidence_never_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_decide(monkeypatch, pilots_dispatch.PilotMode.ON)
    assert (
        await pilots_dispatch.board_evidence_skip(
            MagicMock(), program="scales", evidence_context="  "
        )
        is False
    )
    # The classifier was never consulted for empty evidence.
    assert seen == {}


@pytest.mark.asyncio
async def test_b36_engine_off_spawns(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_on(monkeypatch, False)
    engine = _bare(DispatchWorkEngine)
    mock = AsyncMock()
    monkeypatch.setattr(pilots_dispatch, "board_evidence_skip", mock)
    assert await engine._decisions_board_evidence_skip("coroner", "context") is False
    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# B37 respawn_verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b37_off_shadow_below_floor_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mode, result in [
        (pilots_dispatch.PilotMode.OFF, None),
        (pilots_dispatch.PilotMode.SHADOW, None),
        (
            pilots_dispatch.PilotMode.ON,
            _result(
                {
                    "gate": {
                        "type": "choice",
                        "choice": "hold-task-for-human",
                        "confidence": 0.3,
                    }
                }
            ),
        ),
    ]:
        _patch_decide(monkeypatch, mode, result)
        assert (
            await pilots_dispatch.respawn_verdict(
                MagicMock(),
                agent_slug="be-pm",
                task_id="t",
                task_status="in_progress",
                spawn_attempts=5,
                statuses_seen=["in_progress"],
            )
            is pilots_dispatch.RespawnVerdict.NO_VERDICT
        )


@pytest.mark.asyncio
async def test_b37_confident_hold_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result(
            {
                "gate": {
                    "type": "choice",
                    "choice": "hold-task-for-human",
                    "confidence": 0.9,
                }
            }
        ),
    )
    verdict = await pilots_dispatch.respawn_verdict(
        MagicMock(),
        agent_slug="a",
        task_id="t",
        task_status="x",
        spawn_attempts=5,
        statuses_seen=[],
    )
    assert verdict is pilots_dispatch.RespawnVerdict.HOLD_TASK_FOR_HUMAN


@pytest.mark.asyncio
async def test_b37_breaker_kill_and_amended_fall_back_to_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(DispatchBreakerEngine)

    async def fake_verdict(
        session: object, **kwargs: object
    ) -> pilots_dispatch.RespawnVerdict:
        return pilots_dispatch.RespawnVerdict.KILL_TASK

    monkeypatch.setattr(pilots_dispatch, "respawn_verdict", fake_verdict)

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)
    # kill-task has no breaker mechanic -> the trip STANDS (the stall
    # notice is the human path to cancellation); respawning the identical
    # prompt is exactly the churn the trip exists to stop.
    assert (
        await engine._decisions_respawn_override(
            "be-pm", "t", "in_progress", {"count": 5, "seen_statuses": ["in_progress"]}
        )
        is None
    )


@pytest.mark.asyncio
async def test_b37_trip_consults_coroner_wedged_postmortem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B16 coroner_gate wiring: a breaker trip is an incident class the
    coroner's fixed hooks never see, so the one-shot stall mechanics are
    followed by exactly one decisions-gated postmortem consult."""
    engine = _bare(DispatchBreakerEngine)
    monkeypatch.setattr(engine, "_PM_RESPAWN_MAX_UNPRODUCTIVE", 3, raising=False)
    monkeypatch.setattr(
        engine, "_schedule_respawn_persist", MagicMock(return_value=None)
    )
    monkeypatch.setattr(engine, "_mark_task_stalled", AsyncMock())
    # _notify_stuck_agent lives on another orchestrator mixin, absent from
    # the bare breaker under test.
    monkeypatch.setattr(engine, "_notify_stuck_agent", AsyncMock(), raising=False)
    coroner = AsyncMock(return_value=None)
    monkeypatch.setattr(engine, "_coroner_wedged_postmortem", coroner)
    record = {"count": 5, "seen_statuses": ["in_progress"], "notified": False}
    await engine._pm_trip_stall_notice("be-pm", "t", "in_progress", record)
    coroner.assert_awaited_once()
    assert record["notified"] is True


@pytest.mark.asyncio
async def test_b16_coroner_consult_failure_is_best_effort() -> None:
    """A coroner-side failure (here: an unparseable task id failing UUID
    construction inside the call) degrades to today's stall-notice-only
    behavior: swallowed, logged, never raised into the breaker tick."""
    engine = _bare(DispatchBreakerEngine)
    await engine._coroner_wedged_postmortem(
        "be-pm", "not-a-uuid", "in_progress", {"count": 5, "seen_statuses": []}
    )


@pytest.mark.asyncio
async def test_b37_breaker_no_verdict_keeps_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OFF/SHADOW regression: with the master flag on but the pilot
    unarmed, the pilot resolves NO_VERDICT and the breaker KEEPS its trip
    (the old mapping turned every no-verdict into spawn-anyway, silently
    disabling the wedged-agent protection fleet-wide)."""
    _flag_on(monkeypatch)
    engine = _bare(DispatchBreakerEngine)

    async def fake_verdict(
        session: object, **kwargs: object
    ) -> pilots_dispatch.RespawnVerdict:
        return pilots_dispatch.RespawnVerdict.NO_VERDICT

    monkeypatch.setattr(pilots_dispatch, "respawn_verdict", fake_verdict)

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)
    assert (
        await engine._decisions_respawn_override(
            "be-pm", "t", "in_progress", {"count": 5, "seen_statuses": ["in_progress"]}
        )
        is None
    )


@pytest.mark.asyncio
async def test_b37_breaker_hold_keeps_trip_mechanics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(DispatchBreakerEngine)

    async def fake_verdict(
        session: object, **kwargs: object
    ) -> pilots_dispatch.RespawnVerdict:
        return pilots_dispatch.RespawnVerdict.HOLD_TASK_FOR_HUMAN

    monkeypatch.setattr(pilots_dispatch, "respawn_verdict", fake_verdict)

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)
    assert (
        await engine._decisions_respawn_override(
            "be-pm", "t", "in_progress", {"count": 5, "seen_statuses": []}
        )
        is None
    )


# ---------------------------------------------------------------------------
# B38 submit_now_confidence (gates the brittle proxy both ways)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b38_off_and_below_floor_leave_proxy_in_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mode, result in [
        (pilots_dispatch.PilotMode.OFF, None),
        (pilots_dispatch.PilotMode.SHADOW, None),
        (
            pilots_dispatch.PilotMode.ON,
            _result({"gate": {"type": "score", "score": 2, "confidence": 0.5}}),
        ),
    ]:
        _patch_decide(monkeypatch, mode, result)
        assert await pilots_dispatch.submit_now_confidence(
            MagicMock(), task_id="t", task_state={}
        ) == (None, False)


@pytest.mark.asyncio
async def test_b38_confident_zero_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result({"gate": {"type": "score", "score": 2, "confidence": 0.85}}),
    )
    assert await pilots_dispatch.submit_now_confidence(
        MagicMock(), task_id="t", task_state={}
    ) == (2, True)


@pytest.mark.asyncio
async def test_b38_engine_veto_only_on_confident_work_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(DispatchPromptsEngine)

    @asynccontextmanager
    async def fake_db_ctx() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr("roboco.db.get_db_context", fake_db_ctx)

    async def confident_work(session: object, **kw: object) -> tuple[int | None, bool]:
        return 0, True

    async def confident_conflict(
        session: object, **kw: object
    ) -> tuple[int | None, bool]:
        return 1, True

    async def confident_zero(session: object, **kw: object) -> tuple[int | None, bool]:
        return 2, True

    async def unconfident(session: object, **kw: object) -> tuple[int | None, bool]:
        return None, False

    monkeypatch.setattr(pilots_dispatch, "submit_now_confidence", confident_work)
    assert await engine._decisions_submit_now_veto({}) is True
    # A confident "signals conflict" also vetoes: only a confident 2 keeps
    # the proxy's flip (a wrong auto-submit burns a review cycle).
    monkeypatch.setattr(pilots_dispatch, "submit_now_confidence", confident_conflict)
    assert await engine._decisions_submit_now_veto({}) is True
    monkeypatch.setattr(pilots_dispatch, "submit_now_confidence", confident_zero)
    assert await engine._decisions_submit_now_veto({}) is False
    monkeypatch.setattr(pilots_dispatch, "submit_now_confidence", unconfident)
    assert await engine._decisions_submit_now_veto({}) is False


# ---------------------------------------------------------------------------
# B40 park_cause (additive cause line only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b40_off_shadow_and_below_floor_return_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mode, result in [
        (pilots_dispatch.PilotMode.OFF, None),
        (pilots_dispatch.PilotMode.SHADOW, None),
        (
            pilots_dispatch.PilotMode.ON,
            _result(
                {"gate": {"type": "choice", "choice": "stranded", "confidence": 0.4}}
            ),
        ),
    ]:
        _patch_decide(monkeypatch, mode, result)
        assert (
            await pilots_dispatch.park_cause(
                MagicMock(),
                agent_id="a",
                task_id=None,
                exit_code=1,
                parked_kind=None,
                transcript_tail="boom",
            )
            is None
        )


@pytest.mark.asyncio
async def test_b40_confident_cause_line_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result({"gate": {"type": "choice", "choice": "auth-park", "confidence": 0.9}}),
    )
    verdict = await pilots_dispatch.park_cause(
        MagicMock(),
        agent_id="a",
        task_id=None,
        exit_code=78,
        parked_kind=None,
        transcript_tail=None,
    )
    assert verdict is not None
    cause, line = verdict
    assert cause is pilots_dispatch.ParkCause.AUTH_PARK
    assert "credential" in line


@pytest.mark.asyncio
async def test_b40_engine_off_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_on(monkeypatch, False)
    engine = _bare(SpawnExitEngine)
    engine._assistant_segments_from_transcript = staticmethod(lambda _a: [])
    await engine._decisions_record_exit_cause(
        "a", SimpleNamespace(current_task_id=None), 1
    )
    assert getattr(engine, "_decisions_exit_causes", {}) == {}


@pytest.mark.asyncio
async def test_b40_engine_stashes_cause_for_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_on(monkeypatch)
    engine = _bare(SpawnExitEngine)
    engine._assistant_segments_from_transcript = staticmethod(lambda _a: ["tail"])

    async def fake_cause(
        session: object, **kwargs: object
    ) -> tuple[pilots_dispatch.ParkCause, str]:
        return pilots_dispatch.ParkCause.CRASH_RETRY, "Decisions cause: crash"

    monkeypatch.setattr(pilots_dispatch, "park_cause", fake_cause)

    class _Factory:
        async def __aenter__(self) -> MagicMock:
            return MagicMock()

        async def __aexit__(self, *exc: object) -> bool:
            return False

    import roboco.db.base as db_base

    monkeypatch.setattr(db_base, "get_session_factory", lambda: _Factory)
    await engine._decisions_record_exit_cause(
        "a", SimpleNamespace(current_task_id=None), 1
    )
    assert engine._decisions_exit_causes["a"] == "Decisions cause: crash"


# ---------------------------------------------------------------------------
# B42 pm_closure_confidence (advisory only, chain stays deterministic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b42_off_and_below_floor_no_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mode, result in [
        (pilots_dispatch.PilotMode.OFF, None),
        (pilots_dispatch.PilotMode.SHADOW, None),
        (
            pilots_dispatch.PilotMode.ON,
            _result({"gate": {"type": "score", "score": 2, "confidence": 0.3}}),
        ),
    ]:
        _patch_decide(monkeypatch, mode, result)
        score, line = await pilots_dispatch.closure_safety(
            MagicMock(), task_id="t", team="backend", branch="b", child_count=2
        )
        assert line is None


@pytest.mark.asyncio
async def test_b42_confident_line_logged_and_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result({"gate": {"type": "score", "score": 2, "confidence": 0.9}}),
    )
    score, line = await pilots_dispatch.closure_safety(
        MagicMock(), task_id="t", team="backend", branch="b", child_count=2
    )
    assert score == 2
    assert line is not None and "advisory" in line.lower()


@pytest.mark.asyncio
async def test_b42_engine_off_skips_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_on(monkeypatch, False)
    engine = _bare(DispatchClaimEngine)
    mock = AsyncMock()
    monkeypatch.setattr(pilots_dispatch, "closure_safety", mock)
    assert await engine._decisions_closure_advisory({"id": "t"}) == ""
    mock.assert_not_awaited()


def test_b42_closure_prompt_renders_advisory_line() -> None:
    engine = _bare(DispatchPromptsEngine)
    prompt = engine._build_pm_closure_prompt(
        {"id": "t", "title": "T", "team": "backend"},
        [],
        advisory_line="System advisory: closure-safety signal scores this closure 2/2",
    )
    assert "System advisory" in prompt
    plain = engine._build_pm_closure_prompt(
        {"id": "t", "title": "T", "team": "backend"}, []
    )
    assert "System advisory" not in plain


# ---------------------------------------------------------------------------
# B44 silent_exit (pilot ready; decision point is agent-side, see module doc)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b44_off_shadow_and_no_verdict_clean_substitute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mode, result in [
        (pilots_dispatch.PilotMode.OFF, None),
        (pilots_dispatch.PilotMode.SHADOW, None),
        (pilots_dispatch.PilotMode.ON, _result({"gate": {"type": "noul"}})),
    ]:
        _patch_decide(monkeypatch, mode, result)
        assert (
            await pilots_dispatch.silent_exit_pickup(
                MagicMock(),
                agent_id="a",
                task_id=None,
                stop_attempts=2,
                last_tool=None,
                transcript_tail=None,
            )
            is pilots_dispatch.SilentExitPickup.CLEAN_SUBSTITUTE
        )


@pytest.mark.asyncio
async def test_b44_confident_not_clean_writes_handoff_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result({"gate": {"type": "noul", "noul": 0.1, "confidence": 0.9}}),
    )
    verdict = await pilots_dispatch.silent_exit_pickup(
        MagicMock(),
        agent_id="a",
        task_id=None,
        stop_attempts=2,
        last_tool="Bash",
        transcript_tail="x",
    )
    assert verdict is pilots_dispatch.SilentExitPickup.FIRST_WRITE_HANDOFF_NOTE


@pytest.mark.asyncio
async def test_b44_confident_clean_substitutes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decide(
        monkeypatch,
        pilots_dispatch.PilotMode.ON,
        _result({"gate": {"type": "noul", "noul": 0.9, "confidence": 0.9}}),
    )
    verdict = await pilots_dispatch.silent_exit_pickup(
        MagicMock(),
        agent_id="a",
        task_id=None,
        stop_attempts=2,
        last_tool=None,
        transcript_tail=None,
    )
    assert verdict is pilots_dispatch.SilentExitPickup.CLEAN_SUBSTITUTE


# ---------------------------------------------------------------------------
# Settings flag sanity (unset = OFF = byte-identical behavior)
# ---------------------------------------------------------------------------


def test_b_settings_flag_defaults_off() -> None:
    import roboco.config as cfg

    assert cfg.settings.decisions_enabled is False or isinstance(
        cfg.settings.decisions_enabled, bool
    )
