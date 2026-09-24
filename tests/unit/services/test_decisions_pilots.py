"""Verb/pilot-level tests for the Decisions pilots (spec section 6):
gates, thresholds, shadow semantics, and the baked-in fail-open fallback."""

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import httpx
import pytest
import roboco.config as cfg
from roboco.services.decisions import pilots
from roboco.services.decisions.client import DecisionsClient, DecisionsEndpoint
from roboco.services.decisions.pilots import (
    ParkingLane,
    PilotMode,
    SelfHealGate,
    SteerMode,
    TriageLane,
    complexity_score,
    parking_route,
    preflight_diff,
    self_heal_transient,
    steer_gate,
    triage_failure,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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
) -> DecisionsClient:
    """Arm the pilot stack: master flag on, chosen mode, laya endpoint, and a
    mocked transport returning ``payload`` (or failing)."""
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(pilots, "pilot_mode", AsyncMock(return_value=mode))
    monkeypatch.setattr(pilots, "resolve_endpoint", AsyncMock(return_value=_LAYA))

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=payload or {"answers": {}})

    client = DecisionsClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(pilots, "get_decisions_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_self_heal_high_noul_allows_origination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch, payload=_payload({"gate": {"type": "noul", "noul": 0.9}}))
    verdict = await self_heal_transient(
        cast("AsyncSession", None),
        repo="roboco",
        workflow="ci.yml",
        error_excerpt="Network timeout",
        recent_commit_subjects=["fix: x"],
        attempt_number=1,
        run_id="run-1",
    )
    assert verdict is SelfHealGate.ORIGINATE


@pytest.mark.asyncio
async def test_self_heal_low_noul_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, payload=_payload({"gate": {"type": "noul", "noul": 0.3}}))
    verdict = await self_heal_transient(
        cast("AsyncSession", None),
        repo="roboco",
        workflow="ci.yml",
        error_excerpt="AssertionError",
        recent_commit_subjects=["feat: y"],
        attempt_number=1,
        run_id="run-2",
    )
    assert verdict is SelfHealGate.SKIP


@pytest.mark.asyncio
async def test_self_heal_high_attempt_number_skips_even_when_confident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch, payload=_payload({"gate": {"type": "noul", "noul": 0.95}}))
    verdict = await self_heal_transient(
        cast("AsyncSession", None),
        repo="roboco",
        workflow="ci.yml",
        error_excerpt="timeout",
        recent_commit_subjects=[],
        attempt_number=3,
        run_id="run-3",
    )
    assert verdict is SelfHealGate.SKIP


@pytest.mark.asyncio
async def test_self_heal_backend_failure_is_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch, fail=True)
    verdict = await self_heal_transient(
        cast("AsyncSession", None),
        repo="r",
        workflow="ci.yml",
        error_excerpt="e",
        recent_commit_subjects=[],
        attempt_number=1,
        run_id="run-4",
    )
    assert verdict is SelfHealGate.NO_VERDICT


@pytest.mark.asyncio
async def test_self_heal_shadow_behaves_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload({"gate": {"type": "noul", "noul": 0.9}}),
    )
    verdict = await self_heal_transient(
        cast("AsyncSession", None),
        repo="r",
        workflow="ci.yml",
        error_excerpt="e",
        recent_commit_subjects=[],
        attempt_number=1,
        run_id="run-5",
    )
    assert verdict is SelfHealGate.NO_VERDICT


@pytest.mark.asyncio
async def test_parking_confident_choice_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "retry_soon", "confidence": 0.9}}
        ),
    )
    lane = await parking_route(
        cast("AsyncSession", None),
        agent_slug="backend-dev-1",
        verb="claim",
        task_id="T1",
        upstream_status=429,
        attempts=1,
        minutes_since_first_attempt=0.0,
        fleet_active_tasks=3,
        run_id="run-6",
    )
    assert lane is ParkingLane.RETRY_SOON


@pytest.mark.asyncio
async def test_parking_low_confidence_falls_to_park_standard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "escalate", "confidence": 0.5}}
        ),
    )
    lane = await parking_route(
        cast("AsyncSession", None),
        agent_slug="a",
        verb="claim",
        task_id=None,
        upstream_status=None,
        attempts=3,
        minutes_since_first_attempt=12.0,
        fleet_active_tasks=1,
        run_id="run-7",
    )
    assert lane is ParkingLane.PARK_STANDARD


@pytest.mark.asyncio
async def test_parking_unknown_choice_falls_to_park_standard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "warp_speed", "confidence": 0.99}}
        ),
    )
    lane = await parking_route(
        cast("AsyncSession", None),
        agent_slug="a",
        verb="claim",
        task_id=None,
        upstream_status=None,
        attempts=1,
        minutes_since_first_attempt=0.0,
        fleet_active_tasks=0,
        run_id="run-8",
    )
    assert lane is ParkingLane.PARK_STANDARD


@pytest.mark.asyncio
async def test_complexity_confident_score_used(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload({"gate": {"type": "score", "score": 2.0, "confidence": 0.9}}),
    )
    score, confident = await complexity_score(
        cast("AsyncSession", None),
        task_id="T9",
        task_title="Span three modules",
        task_description="A wide refactor",
        acceptance_criteria=["a"],
        parent_kind="root",
    )
    assert (score, confident) == (2, True)


@pytest.mark.asyncio
async def test_complexity_low_confidence_not_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload({"gate": {"type": "score", "score": 2.0, "confidence": 0.4}}),
    )
    score, confident = await complexity_score(
        cast("AsyncSession", None),
        task_id="T9",
        task_title="t",
        task_description="d",
        acceptance_criteria=[],
        parent_kind=None,
    )
    assert (score, confident) == (None, False)


@pytest.mark.asyncio
async def test_complexity_shadow_returns_nothing_but_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload({"gate": {"type": "score", "score": 1.0, "confidence": 0.9}}),
    )
    score, confident = await complexity_score(
        cast("AsyncSession", None),
        task_id="T9",
        task_title="t",
        task_description="d",
        acceptance_criteria=[],
        parent_kind=None,
    )
    assert (score, confident) == (None, False)


@pytest.mark.asyncio
async def test_preflight_diff_batched_verdicts(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {
        "criterion_0": {"type": "noul", "noul": 0.9, "confidence": 0.9},
        "criterion_1": {"type": "noul", "noul": 0.2, "confidence": 0.8},
        "hygiene": {"type": "noul", "noul": 0.85, "confidence": 0.9},
    }
    _arm(monkeypatch, payload=_payload(answers))
    result = await preflight_diff(
        cast("AsyncSession", None),
        task_id="T1",
        criteria=["b" * 100, "a" * 10],
        diff="diff --git ...",
    )
    assert result is not None
    # Capped at 6, longest first: only the two criteria asked.
    assert len(result["criteria"]) == 2
    assert result["criteria"][0]["addresses"] is True
    assert result["criteria"][1]["addresses"] is False
    assert result["hygiene"]["flagged"] is True


@pytest.mark.asyncio
async def test_triage_confident_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "flaky", "confidence": 0.85}}
        ),
    )
    lane = await triage_failure(
        cast("AsyncSession", None),
        task_id="T1",
        test_name="test_flaky_thing",
        error_excerpt="TimeoutError",
        changed_files_in_diff=["a.py"],
        is_retry=True,
        recent_flake_history_for_test=["flaked 2026-09-01"],
    )
    assert lane is TriageLane.FLAKY


@pytest.mark.asyncio
async def test_triage_low_confidence_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "flaky", "confidence": 0.6}}
        ),
    )
    lane = await triage_failure(
        cast("AsyncSession", None),
        task_id="T1",
        test_name="t",
        error_excerpt="e",
        changed_files_in_diff=[],
        is_retry=False,
        recent_flake_history_for_test=[],
    )
    assert lane is TriageLane.UNKNOWN


@pytest.mark.asyncio
async def test_steer_gate_confident_steering_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {
                "gate": {
                    "type": "choice",
                    "choice": "steer_now",
                    "confidence": 0.9,
                }
            }
        ),
    )
    mode = await steer_gate(
        cast("AsyncSession", None),
        message_id="m1",
        sender="backend-pm-1",
        purpose="correction",
        requires_response=True,
        message_body="The API changed, use v2",
        recipient_context={"current_task_id": "T1"},
    )
    assert mode is SteerMode.STEER_NOW


@pytest.mark.asyncio
async def test_steer_gate_below_floor_falls_to_queue_after_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_payload(
            {
                "gate": {
                    "type": "choice",
                    "choice": "steer_switch_consideration",
                    "confidence": 0.7,
                }
            }
        ),
    )
    mode = await steer_gate(
        cast("AsyncSession", None),
        message_id="m2",
        sender="a",
        purpose=None,
        requires_response=False,
        message_body="hi",
        recipient_context={},
    )
    assert mode is SteerMode.QUEUE_AFTER_CURRENT


@pytest.mark.asyncio
async def test_steer_gate_shadow_never_marks_steering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_payload(
            {"gate": {"type": "choice", "choice": "steer_now", "confidence": 0.95}}
        ),
    )
    mode = await steer_gate(
        cast("AsyncSession", None),
        message_id="m3",
        sender="a",
        purpose=None,
        requires_response=False,
        message_body="x",
        recipient_context={},
    )
    assert mode is SteerMode.QUEUE_AFTER_CURRENT


@pytest.mark.asyncio
async def test_steer_gate_down_is_pull_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, fail=True)
    mode = await steer_gate(
        cast("AsyncSession", None),
        message_id="m4",
        sender="a",
        purpose=None,
        requires_response=False,
        message_body="x",
        recipient_context={},
    )
    assert mode is SteerMode.QUEUE_AFTER_CURRENT


@pytest.mark.asyncio
async def test_flag_off_every_pilot_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", False)
    monkeypatch.setattr(pilots, "resolve_endpoint", AsyncMock(return_value=_LAYA))
    mode, result = await pilots.decide_for_pilot(
        cast("AsyncSession", None), "self_heal", {}, {}, "s"
    )
    assert mode is PilotMode.OFF
    assert result is None


# ---------------------------------------------------------------------------
# B27 tool_spotlight (spawn-briefing verb highlight)
# ---------------------------------------------------------------------------

_SPOTLIGHT_VERBS = [
    "claim",
    "evidence",
    "note",
    "dm",
    "pass",
    "read_a2a",
    "notify",
    "unclaim",
    "submit",
    "fail",
]


def _spotlight_payload(confidence: float, probabilities: dict) -> dict:
    return _payload(
        {
            "gate": {
                "type": "choice",
                "choice": "claim",
                "confidence": confidence,
                "probabilities": probabilities,
            }
        }
    )


@pytest.mark.asyncio
async def test_tool_spotlight_picks_top_verbs_from_probabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        payload=_spotlight_payload(
            0.8,
            {
                "claim": 0.9,
                "evidence": 0.8,
                "note": 0.7,
                "dm": 0.6,
                "pass": 0.5,
                "read_a2a": 0.4,
                "notify": 0.3,
                "unclaim": 0.2,
                "submit": 0.0,
                "fail": 0.1,
            },
        ),
    )
    verdict = await pilots.tool_spotlight(
        cast("AsyncSession", None),
        agent_slug="dev-backend-1",
        task_title="Add endpoint",
        task_description="POST /widgets",
        verbs=list(_SPOTLIGHT_VERBS),
    )
    assert verdict == [
        "claim",
        "evidence",
        "note",
        "dm",
        "pass",
        "read_a2a",
        "notify",
    ]


@pytest.mark.asyncio
async def test_tool_spotlight_returns_none_below_confidence_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch, payload=_spotlight_payload(0.5, {"claim": 0.9}))
    verdict = await pilots.tool_spotlight(
        cast("AsyncSession", None),
        agent_slug="dev-backend-1",
        task_title="Add endpoint",
        task_description="POST /widgets",
        verbs=list(_SPOTLIGHT_VERBS),
    )
    assert verdict is None


@pytest.mark.asyncio
async def test_tool_spotlight_returns_none_outside_the_option_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 5 verbs: too few to be worth a call; 25 verbs: past the ~20-option
    # degradation limit (spec 10 limit 3). Both return None before any call.
    _arm(monkeypatch, payload=_spotlight_payload(0.9, {}))
    for verbs in ([f"v{i}" for i in range(5)], [f"v{i}" for i in range(25)]):
        verdict = await pilots.tool_spotlight(
            cast("AsyncSession", None),
            agent_slug="dev-backend-1",
            task_title="Add endpoint",
            task_description="POST /widgets",
            verbs=verbs,
        )
        assert verdict is None


@pytest.mark.asyncio
async def test_tool_spotlight_shadow_logs_but_renders_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(
        monkeypatch,
        mode=PilotMode.SHADOW,
        payload=_spotlight_payload(
            0.9,
            {
                "claim": 0.9,
                "evidence": 0.8,
                "note": 0.7,
                "dm": 0.6,
                "pass": 0.5,
                "read_a2a": 0.4,
                "notify": 0.3,
            },
        ),
    )
    verdict = await pilots.tool_spotlight(
        cast("AsyncSession", None),
        agent_slug="dev-backend-1",
        task_title="Add endpoint",
        task_description="POST /widgets",
        verbs=list(_SPOTLIGHT_VERBS),
    )
    assert verdict is None
