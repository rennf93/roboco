"""Gateway-lane Decisions pilots (spec 7.1 Tier B rows B1, B20, B31, B34,
B39, B41, B43): fail-open semantics at the seam.

One test per row covering the four mandated postures: off mode = the
guarded call site returns today's result WITHOUT any decisions call,
shadow acts as off, a confident verdict applies, and below-floor falls
back. Verdicts are pinned through the real ``decide_for_pilot`` path by
monkeypatching the shared ``pilot_mode`` chokepoint and the decisions
client, per tests/unit/services/test_decisions_wiring.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.decisions import pilots
from roboco.services.decisions import pilots_gateway as pg
from roboco.services.decisions.schemas import DecisionAnswer, DecisionResult
from roboco.services.gateway.choreographer import Choreographer
from roboco.services.gateway.commit_validator import (
    validate_commit_message_with_intent,
)
from roboco.services.gateway.evidence_builder import build_evidence_for_task
from structlog.testing import capture_logs

PILOTS = "roboco.services.decisions.pilots"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(**answers: dict[str, Any]) -> DecisionResult:
    """A DecisionResult with the given typed answers."""
    built = {}
    for key, fields_raw in answers.items():
        fields = dict(fields_raw)
        fields.setdefault("type", "noul")
        built[key] = DecisionAnswer(key=key, **fields)
    return DecisionResult(answers=built, tier="test", session_id="sess")


def _pin_verdict(
    monkeypatch: pytest.MonkeyPatch,
    mode: pilots.PilotMode,
    result: DecisionResult | None,
) -> AsyncMock:
    """Pin a pilot posture through the real decide_for_pilot path: the
    shared pilot_mode chokepoint plus a mocked decisions client. Returns
    the client's ``decide`` mock so tests can assert it never fired."""
    endpoint = MagicMock()

    async def _mode(_session: object, _pilot: str) -> pilots.PilotMode:
        return mode

    async def _resolve(_session: object) -> MagicMock:
        return endpoint

    decide = AsyncMock(return_value=result)
    client = MagicMock()
    client.decide = decide
    monkeypatch.setattr(f"{PILOTS}.pilot_mode", _mode)
    monkeypatch.setattr(f"{PILOTS}.resolve_endpoint", _resolve)
    monkeypatch.setattr(f"{PILOTS}.get_decisions_client", lambda: client)
    return decide


def _choreographer() -> Choreographer:
    """A Choreographer with bare mocks (no DB), per test_decisions_wiring."""
    choreo = object.__new__(Choreographer)
    deps = MagicMock()
    deps.task = AsyncMock()
    deps.task.session = MagicMock()
    deps.git = AsyncMock()
    deps.evidence_repo = AsyncMock()
    choreo._deps = deps
    return choreo


def _owned_task(**extra: Any) -> MagicMock:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(),
        "title": "Implement retry backoff",
        "status": "in_progress",
        "commits": [{"hash": "abc12345"}],
        "pr_number": None,
        "branch_name": "feature/backend/abc12345",
        "updated_at": now,
        "intends_to_touch": ["roboco/services/retry.py"],
        "description": "Add retry backoff",
        "plan": "retry with exponential backoff",
    }
    base.update(extra)
    return MagicMock(**base)


_STRANDED = {
    "type": "choice",
    "choice": "likely-stranded-escalate",
    "confidence": 0.9,
}
_DONE = {
    "type": "choice",
    "choice": "likely-done-submit-now",
    "confidence": 0.9,
}


# ---------------------------------------------------------------------------
# B1 plan_quality: PM plan gate scoring lane
# ---------------------------------------------------------------------------


async def _call_plan_quality_gate(choreo: Choreographer):
    return await choreo._plan_quality_gate(
        role_str="cell_pm",
        rich_plan={
            "approach": "a" * 150,
            "sub_tasks": [{"title": "t", "description": "d" * 60}],
        },
        task=MagicMock(id=uuid4(), title="Decompose the work"),
        agent_id=uuid4(),
        task_id=uuid4(),
        briefing={},
    )


@pytest.mark.asyncio
async def test_b1_plan_quality_postures(monkeypatch: pytest.MonkeyPatch):
    choreo = _choreographer()

    # OFF: the gate passes without any decisions call.
    decide = _pin_verdict(monkeypatch, pilots.PilotMode.OFF, None)
    assert await _call_plan_quality_gate(choreo) is None
    decide.assert_not_awaited()

    # SHADOW: a confident LOW score still behaves as off.
    decide = _pin_verdict(
        monkeypatch,
        pilots.PilotMode.SHADOW,
        _result(gate={"type": "score", "score": 0, "confidence": 0.9}),
    )
    assert await _call_plan_quality_gate(choreo) is None
    decide.assert_awaited_once()

    # ON + confident LOW score: the padded plan is REJECTED (strictly).
    decide = _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(gate={"type": "score", "score": 0, "confidence": 0.9}),
    )
    env = await _call_plan_quality_gate(choreo)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "incomplete_input"
    assert "plan_quality" in (body.get("missing") or [])

    # ON + below floor (0.7): falls back to the count-based pass.
    decide = _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(gate={"type": "score", "score": 0, "confidence": 0.5}),
    )
    assert await _call_plan_quality_gate(choreo) is None


# ---------------------------------------------------------------------------
# B20 commit_intent: advisory hint in the commit validator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b20_commit_intent_postures(monkeypatch: pytest.MonkeyPatch):
    # OFF: structural result unchanged, no decisions call.
    decide = _pin_verdict(monkeypatch, pilots.PilotMode.OFF, None)
    res = await validate_commit_message_with_intent(
        MagicMock(),
        "feat(api): add liveness probe endpoint",
        diff_summary="diff --git a/api.py",
    )
    assert res.ok and res.evidence is None
    decide.assert_not_awaited()

    # SHADOW: acts as off (no hint) even on a confident mismatch.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.SHADOW,
        _result(gate={"type": "noul", "noul": 0.1, "confidence": 0.95}),
    )
    res = await validate_commit_message_with_intent(
        MagicMock(), "feat(api): add liveness probe endpoint", diff_summary="d"
    )
    assert res.ok and res.evidence is None

    # ON + confident mismatch: advisory evidence hint, shape check intact.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(gate={"type": "noul", "noul": 0.1, "confidence": 0.95}),
    )
    res = await validate_commit_message_with_intent(
        MagicMock(), "feat(api): add liveness probe endpoint", diff_summary="d"
    )
    assert res.ok
    assert res.evidence == {"intent_hint": pg.COMMIT_INTENT_HINT}

    # Below floor (the model unsure about the mismatch): no hint.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(gate={"type": "noul", "noul": 0.6, "confidence": 0.5}),
    )
    res = await validate_commit_message_with_intent(
        MagicMock(), "feat(api): add liveness probe endpoint", diff_summary="d"
    )
    assert res.ok and res.evidence is None

    # The structural check is never bypassed: a banned message still fails.
    res = await validate_commit_message_with_intent(MagicMock(), "wip")
    assert not res.ok

    # No diff signal at all (no summary, no files): no call, no hint.
    decide = _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(gate={"type": "noul", "noul": 0.1, "confidence": 0.95}),
    )
    res = await validate_commit_message_with_intent(
        MagicMock(), "feat(api): add liveness probe endpoint"
    )
    assert res.ok and res.evidence is None
    decide.assert_not_awaited()


# ---------------------------------------------------------------------------
# B31 findings_mapping: suggested map + zero-overlap warnings
# ---------------------------------------------------------------------------


def _finding() -> dict[str, Any]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "file": "roboco/services/retry.py",
        "line": 12,
        "expected": "retry honors the deadline",
        "actual": "retry loops forever",
    }


@pytest.mark.asyncio
async def test_b31_findings_mapping_postures(monkeypatch: pytest.MonkeyPatch):
    # OFF: no decisions call at all.
    decide = _pin_verdict(monkeypatch, pilots.PilotMode.OFF, None)
    assert (
        await pg.findings_mapping(
            MagicMock(), task_id="t", findings=[_finding()], diff="d", files_changed=[]
        )
        is None
    )
    decide.assert_not_awaited()

    # SHADOW: acts as off even with a confident hit.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.SHADOW,
        _result(finding_0={"type": "noul", "noul": 0.9, "confidence": 0.9}),
    )
    assert (
        await pg.findings_mapping(
            MagicMock(), task_id="t", findings=[_finding()], diff="d", files_changed=[]
        )
        is None
    )

    # ON + confident: the suggested map includes the finding with its
    # overlapping diff files.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(finding_0={"type": "noul", "noul": 0.9, "confidence": 0.9}),
    )
    mapping = await pg.findings_mapping(
        MagicMock(),
        task_id="t",
        findings=[_finding()],
        diff="d",
        files_changed=["roboco/services/retry.py"],
    )
    assert mapping is not None and len(mapping) == 1
    assert mapping[0]["finding_id"] == _finding()["id"]
    assert mapping[0]["files"] == ["roboco/services/retry.py"]

    # ON + below the 0.6 per-finding floor: excluded from the map.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(finding_0={"type": "noul", "noul": 0.4, "confidence": 0.9}),
    )
    mapping = await pg.findings_mapping(
        MagicMock(), task_id="t", findings=[_finding()], diff="d", files_changed=[]
    )
    assert mapping == []

    # Call-site OFF: the advisory returns without touching the task/git.
    decide = _pin_verdict(monkeypatch, pilots.PilotMode.OFF, None)
    choreo = _choreographer()
    row = MagicMock(id="11111111-1111-1111-1111-111111111111", file="retry.py")
    await choreo._findings_mapping_advisory(uuid4(), [row])
    choreo._deps.task.get.assert_not_awaited()
    decide.assert_not_awaited()

    # Call-site ON with an empty map: every resolved finding warns
    # (advisory only; the ledger semantics are untouched).
    _pin_verdict(monkeypatch, pilots.PilotMode.ON, _result())
    choreo._deps.task.get.return_value = _owned_task()
    choreo._deps.git.diff_and_files.return_value = ("diff", ["retry.py"])
    with capture_logs() as logs:
        await choreo._findings_mapping_advisory(uuid4(), [row])
    assert any(e.get("event") == "resolved_finding_zero_diff_overlap" for e in logs), (
        logs
    )


# ---------------------------------------------------------------------------
# B34 idle_legitimacy: escalate or hint, never block a legit idle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b34_idle_legitimacy_postures(monkeypatch: pytest.MonkeyPatch):
    choreo = _choreographer()
    choreo._deps.task.list_in_progress_for_agent.return_value = [_owned_task()]
    notify = MagicMock()
    monkeypatch.setattr("roboco.services.notification.NotificationService", notify)
    sent = AsyncMock()
    notify.return_value.send_ack_notification = sent

    # OFF: no owned-state fetch, no decisions call.
    decide = _pin_verdict(monkeypatch, pilots.PilotMode.OFF, None)
    assert await choreo._idle_legitimacy_hint(uuid4()) is None
    choreo._deps.task.list_in_progress_for_agent.assert_not_awaited()
    decide.assert_not_awaited()

    # SHADOW: a confident stranded verdict escalates nothing.
    _pin_verdict(monkeypatch, pilots.PilotMode.SHADOW, _result(gate=_STRANDED))
    assert await choreo._idle_legitimacy_hint(uuid4()) is None
    sent.assert_not_awaited()

    # ON + confident stranded: a PM notification is sent (may only add).
    _pin_verdict(monkeypatch, pilots.PilotMode.ON, _result(gate=_STRANDED))
    assert await choreo._idle_legitimacy_hint(uuid4()) is None
    sent.assert_awaited_once()
    assert "Idle-legitimacy" in sent.await_args.kwargs["body"]

    # ON + confident likely-done: a `next` hint naming i_am_done.
    _pin_verdict(monkeypatch, pilots.PilotMode.ON, _result(gate=_DONE))
    hint = await choreo._idle_legitimacy_hint(uuid4())
    assert hint is not None and "i_am_done(task_id=" in hint

    # ON + legit-wait: nothing (a legit idle is never blocked or decorated).
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(gate={"type": "choice", "choice": "legit-wait", "confidence": 0.9}),
    )
    assert await choreo._idle_legitimacy_hint(uuid4()) is None

    # Below the 0.7 floor: falls back to today's idle.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(
            gate={
                "type": "choice",
                "choice": "likely-stranded-escalate",
                "confidence": 0.5,
            }
        ),
    )
    assert await choreo._idle_legitimacy_hint(uuid4()) is None
    sent.assert_awaited_once()  # still just the earlier escalation


# ---------------------------------------------------------------------------
# B39 branch_staleness: mid-work notification, never a block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b39_branch_staleness_postures(monkeypatch: pytest.MonkeyPatch):
    choreo = _choreographer()
    t = _owned_task()
    choreo._deps.git.is_behind_base.return_value = (3, 2)
    choreo._deps.task.agent_for.return_value = MagicMock(slug="be-dev-1")
    notify = MagicMock()
    monkeypatch.setattr("roboco.services.notification.NotificationService", notify)
    sent = AsyncMock()
    notify.return_value.send_ack_notification = sent

    async def _resolve(_t: object, _svc: object) -> str:
        return "feature/backend/parent12345"

    monkeypatch.setattr(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        _resolve,
    )

    def _verdict(noul: float, confidence: float) -> DecisionResult:
        return _result(
            risk={"type": "noul", "noul": noul, "confidence": confidence},
            severity={"type": "score", "score": 2, "confidence": confidence},
        )

    # OFF: the gate's git data is never even fetched.
    decide = _pin_verdict(monkeypatch, pilots.PilotMode.OFF, None)
    await choreo._branch_staleness_advisory(uuid4(), t)
    choreo._deps.git.is_behind_base.assert_not_awaited()
    decide.assert_not_awaited()

    # SHADOW: behind branch, confident risk - still no notification.
    _pin_verdict(monkeypatch, pilots.PilotMode.SHADOW, _verdict(0.95, 0.9))
    await choreo._branch_staleness_advisory(uuid4(), t)
    sent.assert_not_awaited()

    # ON + confident: the dev is notified (additive only).
    _pin_verdict(monkeypatch, pilots.PilotMode.ON, _verdict(0.95, 0.9))
    await choreo._branch_staleness_advisory(uuid4(), t)
    sent.assert_awaited_once()
    assert sent.await_args.kwargs["to_agent"] == "be-dev-1"

    # ON + low noul: nothing further.
    _pin_verdict(monkeypatch, pilots.PilotMode.ON, _verdict(0.4, 0.9))
    await choreo._branch_staleness_advisory(uuid4(), t)
    sent.assert_awaited_once()

    # Below the confidence floor: nothing.
    _pin_verdict(monkeypatch, pilots.PilotMode.ON, _verdict(0.95, 0.5))
    await choreo._branch_staleness_advisory(uuid4(), t)
    sent.assert_awaited_once()


# ---------------------------------------------------------------------------
# B41 lesson_prune: prune-only applicability scoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b41_lesson_prune_postures(monkeypatch: pytest.MonkeyPatch):
    choreo = _choreographer()
    t = _owned_task()
    items = [{"title": "lesson-a"}, {"title": "lesson-b"}]

    # OFF (call site): the injected list is untouched, no decisions call.
    decide = _pin_verdict(monkeypatch, pilots.PilotMode.OFF, None)
    assert await choreo._prune_institutional_memory(t, items) is items
    decide.assert_not_awaited()

    # SHADOW: acts as off - everything is injected.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.SHADOW,
        _result(
            lesson_0={"type": "score", "score": 0, "confidence": 0.9},
            lesson_1={"type": "score", "score": 2, "confidence": 0.9},
        ),
    )
    assert await choreo._prune_institutional_memory(t, items) is items

    # ON: only the inapplicable lesson (score 0 -> 0.0 < 0.4) is pruned.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(
            lesson_0={"type": "score", "score": 0, "confidence": 0.9},
            lesson_1={"type": "score", "score": 2, "confidence": 0.9},
        ),
    )
    kept = await choreo._prune_institutional_memory(t, items)
    assert kept == [items[1]]

    # Boundary: score 1 normalizes to 0.5, at/above the 0.4 keep floor.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(
            lesson_0={"type": "score", "score": 1, "confidence": 0.9},
            lesson_1={"type": "score", "score": 1, "confidence": 0.9},
        ),
    )
    kept = await choreo._prune_institutional_memory(t, items)
    assert kept == items

    # No verdict: inject all, exactly today.
    _pin_verdict(monkeypatch, pilots.PilotMode.ON, None)
    assert await choreo._prune_institutional_memory(t, items) is items


# ---------------------------------------------------------------------------
# B43 assembled_coherence: advisory scaffold in the gate claim envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b43_assembled_coherence_postures(monkeypatch: pytest.MonkeyPatch):
    choreo = _choreographer()

    # OFF: omitted, no decisions call.
    decide = _pin_verdict(monkeypatch, pilots.PilotMode.OFF, None)
    assert await choreo._coherence_scaffold(_owned_task(), "diff", ["a.py"]) is None
    decide.assert_not_awaited()

    # SHADOW: acts as off.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.SHADOW,
        _result(gate={"type": "score", "score": 0, "confidence": 0.9}),
    )
    assert await choreo._coherence_scaffold(_owned_task(), "diff", ["a.py"]) is None

    # ON + confident: the scaffold is surfaced for the reviewer.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(gate={"type": "score", "score": 0, "confidence": 0.9}),
    )
    verdict = await choreo._coherence_scaffold(_owned_task(), "diff", ["a.py"])
    assert verdict == {"score": 0, "confidence": 0.9}

    # Below the 0.7 floor: the field is omitted.
    _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(gate={"type": "score", "score": 0, "confidence": 0.5}),
    )
    assert await choreo._coherence_scaffold(_owned_task(), "diff", ["a.py"]) is None

    # No diff (branchless or fetch failure): omitted without a call.
    decide = _pin_verdict(
        monkeypatch,
        pilots.PilotMode.ON,
        _result(gate={"type": "score", "score": 0, "confidence": 0.9}),
    )
    assert await choreo._coherence_scaffold(_owned_task(), "", []) is None
    decide.assert_not_awaited()


# ---------------------------------------------------------------------------
# evidence plumb-through for B31's suggested_findings_mapping
# ---------------------------------------------------------------------------


def test_evidence_carries_suggested_findings_mapping_when_present() -> None:
    t = _owned_task(pr_number=7)
    payload = build_evidence_for_task(
        t,
        journal_highlights=[],
        files_changed=["retry.py"],
        suggested_findings_mapping=[{"finding_id": "aa", "files": ["retry.py"]}],
    )
    assert payload.as_dict()["suggested_findings_mapping"] == [
        {"finding_id": "aa", "files": ["retry.py"]}
    ]

    # Default (pilot off): the key is omitted entirely (zero token cost).
    payload = build_evidence_for_task(
        t, journal_highlights=[], files_changed=["retry.py"]
    )
    assert "suggested_findings_mapping" not in payload.as_dict()
