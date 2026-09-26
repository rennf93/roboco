"""The Decisions pilots, one function per integration point (spec section 6).

Every pilot bakes the fail-open fallback in: callers never see raw Jev
types, and a ``None``/below-threshold/low-confidence verdict means "do
exactly what you did before this service existed". Shadow mode logs the
verdict and the action the pilot WOULD have taken, then behaves like
``off``; nothing materializes on a verdict alone in any mode.

Per-pilot modes live in the settings store as ``decisions.pilot.{slug}`` =
off | shadow | on (effective on backend restart), resolved through the
single chokepoint ``pilot_mode`` here, mirroring the board-program
enablement pattern.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

from roboco.config import settings
from roboco.services.decisions.client import (
    DecisionsEndpoint,
    get_decisions_client,
)
from roboco.services.decisions.resolver import resolve_endpoint
from roboco.services.decisions.schemas import (
    ChoiceQuestion,
    DecisionQuestion,
    DecisionResult,
    NoulQuestion,
    ScoreQuestion,
)

logger = structlog.get_logger(__name__)


class PilotMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ON = "on"


PILOT_SLUGS = (
    "self_heal",
    "parking",
    "complexity",
    "preflight_diff",
    "triage_failure",
    "steer_gate",
    "transcript_notes",
    "tool_spotlight",
)


class SelfHealGate(StrEnum):
    """Verdict for the transient-failure noul (6.1)."""

    ORIGINATE = "originate"
    SKIP = "skip"
    NO_VERDICT = "no_verdict"


class ParkingLane(StrEnum):
    """Verdict for the rate-limit/collision routing choice (6.2)."""

    PARK_STANDARD = "park_standard"
    RETRY_SOON = "retry_soon"
    ESCALATE = "escalate"


class TriageLane(StrEnum):
    """Verdict for the red-test triage choice (6.5)."""

    MY_REGRESSION = "my_regression"
    FLAKY = "flaky"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"


class SteerMode(StrEnum):
    """Verdict for the A2A steer gate (6.6)."""

    STEER_SWITCH_CONSIDERATION = "steer_switch_consideration"
    STEER_NOW = "steer_now"
    QUEUE_AFTER_CURRENT = "queue_after_current"
    FYI_PULL = "fyi_pull"


# Thresholds (spec section 6). Deliberately tight where a false positive
# spawns churn (self-heal), loosest where all lanes are already legal
# (parking). Calibrate from logged shadow data, never vibes.
SELF_HEAL_NOUL_FLOOR = 0.85
SELF_HEAL_MAX_ATTEMPT = 2
PARKING_CONFIDENCE_FLOOR = 0.7
COMPLEXITY_CONFIDENCE_FLOOR = 0.7
TRIAGE_CONFIDENCE_FLOOR = 0.7
STEER_CONFIDENCE_FLOOR = 0.8
PREFLIGHT_ADVISORY_CONFIDENCE_FLOOR = 0.7
TRANSCRIPT_NOTE_FLOOR = 0.75


def _env_slug_set(raw: str) -> frozenset[str]:
    """Parse a comma-separated env slug list (empty -> empty set)."""
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


async def pilot_mode(session: AsyncSession, pilot: str) -> PilotMode:
    """THE single chokepoint every pilot mode read routes through. The
    master flag off forces OFF regardless of anything else. Resolution:
    settings-store row (set from the panel) > env slug lists
    (``decisions_pilots_on`` / ``decisions_pilots_shadow``, the operator
    deploy arming path) > OFF, which is exactly the pre-Decisions
    behavior."""
    if not settings.decisions_enabled:
        return PilotMode.OFF
    from roboco.services.settings import get_settings_service

    raw = await get_settings_service(session).get(f"decisions.pilot.{pilot}")
    if raw is not None:
        try:
            return PilotMode(raw.strip().lower())
        except ValueError:
            return PilotMode.OFF
    if pilot in _env_slug_set(settings.decisions_pilots_on):
        return PilotMode.ON
    if pilot in _env_slug_set(settings.decisions_pilots_shadow):
        return PilotMode.SHADOW
    return PilotMode.OFF


async def decide_for_pilot(
    session: AsyncSession,
    pilot: str,
    state: object,
    questions: Mapping[str, DecisionQuestion],
    session_id: str,
) -> tuple[PilotMode, DecisionResult | None]:
    """Resolve mode + tier, ask one batched question set, log the verdict.

    The prologue's DB reads (settings row, provider key) run on the
    CALLER's shared, reused session inside a savepoint (repo
    shared-session discipline): a failed read rolls the savepoint back
    and this returns OFF instead of leaving the caller's transaction
    poisoned for its next statement. A ``None`` session (test stubs;
    session-less callers now open their own) skips the savepoint. The
    network call itself runs AFTER the savepoint is released. ``result``
    is ``None`` when off/unreachable/failed. Shadow callers act as if
    the verdict never happened (the log line above is the shadow data);
    on callers gate on it.
    """

    async def _prologue() -> tuple[PilotMode, DecisionsEndpoint | None]:
        resolved_mode = await pilot_mode(session, pilot)
        if resolved_mode is PilotMode.OFF:
            return resolved_mode, None
        return resolved_mode, await resolve_endpoint(session)

    try:
        begin_nested = getattr(session, "begin_nested", None)
        if begin_nested is not None:
            async with begin_nested():
                mode, endpoint = await _prologue()
        else:
            # Test stubs pass a bare None session; production sessions
            # always carry begin_nested.
            mode, endpoint = await _prologue()
    except Exception as exc:
        logger.warning(
            "decisions prologue failed on the caller's session; pilot off (fail-open)",
            pilot=pilot,
            error=str(exc),
        )
        return PilotMode.OFF, None
    if endpoint is None:
        return mode, None
    result = await get_decisions_client().decide(
        endpoint, state, questions, session_id=f"{pilot}:{session_id}"
    )
    if result is None:
        logger.info(
            "decision unavailable; pilot falls back to baseline behavior",
            pilot=pilot,
            mode=mode.value,
        )
    return mode, result


def _confidence(result: DecisionResult, key: str) -> float | None:
    answer = result.answer(key)
    if answer is None:
        return None
    return answer.confidence


def _gate_choice(result: DecisionResult) -> tuple[str | None, float | None]:
    """(choice, confidence) off the single "gate" answer."""
    answer = result.answer("gate")
    return (
        answer.choice if answer else None,
        answer.confidence if answer else None,
    )


def _gate_score(result: DecisionResult) -> tuple[float | None, float | None]:
    """(score, confidence) off the single "gate" answer."""
    answer = result.answer("gate")
    return (
        answer.score if answer else None,
        answer.confidence if answer else None,
    )


def log_action(
    pilot: str,
    mode: PilotMode,
    verdict: object,
    action: str,
    result: DecisionResult | None,
) -> None:
    """The audit-trail line: pilot, verdict, confidence, action taken (or
    would-have-taken in shadow), cost, active tier. Also the single
    persistence chokepoint: every pilot's verdict lands in decision_log
    (fire-and-forget; the Auditor's daily review reads it)."""
    logger.info(
        "decision action",
        pilot=pilot,
        mode=mode.value,
        verdict=verdict,
        action=action,
        tier=result.tier if result else None,
        confidence=_confidence(result, "gate") if result else None,
        cost=result.usage.cost if result else None,
        session_id=result.session_id if result else None,
    )
    try:
        from roboco.services.decisions.persist import record_decision

        record_decision(
            pilot=pilot,
            mode=mode.value,
            verdict=verdict,
            action=action,
            result=result,
        )
    except Exception as exc:  # evidence must never break a decision
        logger.debug("decision_log persist skipped", pilot=pilot, error=str(exc))


# ---------------------------------------------------------------------------
# 6.1 self_heal: transient-failure noul
# ---------------------------------------------------------------------------


async def self_heal_transient(
    session: AsyncSession,
    *,
    repo: str,
    workflow: str,
    error_excerpt: str,
    recent_commit_subjects: list[str],
    attempt_number: int,
    run_id: str,
) -> SelfHealGate:
    """Gate the self-heal origination on a transient-vs-regression verdict.

    ``ORIGINATE`` means the engine may originate the fix task exactly as
    today; ``SKIP`` means log and skip (no task this sweep); ``NO_VERDICT``
    means Decisions had nothing to say and the engine does what it did
    before. The gate direction (affirmed-transience -> originate) and the
    thresholds come straight from spec 6.1; shadow data is the referee.
    """
    state = {
        "repo": repo,
        "workflow": workflow,
        "error_excerpt": error_excerpt,
        "recent_commit_subjects": recent_commit_subjects,
        "attempt_number": attempt_number,
    }
    questions = {
        "gate": NoulQuestion(
            instructions=(
                "This failure is transient (infra/flake/network) rather than "
                "a defect in the recent commits."
            )
        ),
    }
    mode, result = await decide_for_pilot(
        session, "self_heal", state, questions, session_id=f"selfheal:{run_id}"
    )
    if result is None:
        return SelfHealGate.NO_VERDICT
    answer = result.answer("gate")
    noul = answer.noul if answer else None
    if noul is None:
        return SelfHealGate.NO_VERDICT
    if noul >= SELF_HEAL_NOUL_FLOOR and attempt_number <= SELF_HEAL_MAX_ATTEMPT:
        verdict = SelfHealGate.ORIGINATE
    else:
        verdict = SelfHealGate.SKIP
    if mode is PilotMode.SHADOW:
        # Shadow: log the would-be action, behave as today.
        log_action("self_heal", mode, verdict.value, "no-op (shadow)", result)
        return SelfHealGate.NO_VERDICT
    log_action(
        "self_heal",
        mode,
        verdict.value,
        "allow origination"
        if verdict is SelfHealGate.ORIGINATE
        else "skip origination",
        result,
    )
    return verdict


# ---------------------------------------------------------------------------
# 6.2 parking: rate-limit/collision routing choice
# ---------------------------------------------------------------------------


def _parking_verdict(choice: str | None, confidence: float | None) -> ParkingLane:
    """The parsed lane, dropped to park_standard when below the floor."""
    try:
        verdict = (
            ParkingLane(choice) if choice is not None else ParkingLane.PARK_STANDARD
        )
    except ValueError:
        verdict = ParkingLane.PARK_STANDARD
    if verdict is not ParkingLane.PARK_STANDARD and (
        confidence is None or confidence < PARKING_CONFIDENCE_FLOOR
    ):
        verdict = ParkingLane.PARK_STANDARD
    return verdict


async def parking_route(
    session: AsyncSession,
    *,
    agent_slug: str,
    verb: str,
    task_id: str | None,
    upstream_status: int | None,
    attempts: int,
    minutes_since_first_attempt: float,
    fleet_active_tasks: int,
    run_id: str,
) -> ParkingLane:
    """Pick a routing lane for a rate-limit/collision park. All three lanes
    are already legal behaviors; the verdict only picks between them, and
    below-threshold falls to ``park_standard`` (today's behavior)."""
    state = {
        "agent_slug": agent_slug,
        "verb": verb,
        "task_id": task_id,
        "upstream_status": upstream_status,
        "attempts": attempts,
        "minutes_since_first_attempt": round(minutes_since_first_attempt, 1),
        "fleet_active_tasks": fleet_active_tasks,
    }
    questions = {
        "gate": ChoiceQuestion(
            instructions="Which handling applies to this rate-limit/collision park?",
            criteria={
                "park_standard": (
                    "Standard cooldown backoff: wait out the usual retry-after "
                    "window before the next attempt."
                ),
                "retry_soon": (
                    "A short retry is likely to succeed (e.g. a rolling limit "
                    "just reset); repark with a much shorter retry-after."
                ),
                "escalate": (
                    "Repeated failures worth a PM notification rather than "
                    "another silent park."
                ),
            },
        ),
    }
    mode, result = await decide_for_pilot(
        session, "parking", state, questions, session_id=f"parking:{run_id}"
    )
    if result is None:
        return ParkingLane.PARK_STANDARD
    choice, confidence = _gate_choice(result)
    verdict = _parking_verdict(choice, confidence)
    if mode is PilotMode.SHADOW:
        log_action("parking", mode, verdict.value, "no-op (shadow)", result)
        return ParkingLane.PARK_STANDARD
    log_action("parking", mode, verdict.value, f"lane={verdict.value}", result)
    return verdict


# ---------------------------------------------------------------------------
# 6.3 complexity: spawn-time routing score
# ---------------------------------------------------------------------------


async def complexity_score(
    session: AsyncSession,
    *,
    task_id: str,
    task_title: str,
    task_description: str,
    acceptance_criteria: list[str],
    parent_kind: str | None,
) -> tuple[int | None, bool]:
    """Score task complexity 0-2 for the ``ROLE:complexity`` routing lookup.

    Returns ``(score, confident)``; ``(None, False)`` when no verdict. The
    caller uses the scored tier only when ``confident`` (and mode is ON);
    otherwise the static default tier. Shadow logs and behaves as off.
    """
    state = {
        "task_title": task_title,
        "task_description": task_description,
        "acceptance_criteria": acceptance_criteria,
        "parent_kind": parent_kind,
    }
    questions = {
        "gate": ScoreQuestion(
            instructions="How heavy is this task?",
            criteria=[
                "Light, mechanical: small, well-scoped, single-file or config work",
                "Standard delivery: normal feature/bug scope within one cell",
                "Heavy, multi-system: spans modules or projects, wide blast radius",
            ],
        ),
    }
    mode, result = await decide_for_pilot(
        session, "complexity", state, questions, session_id=f"complexity:{task_id}"
    )
    if result is None:
        return None, False
    score, confidence = _gate_score(result)
    if score is None or confidence is None or confidence < COMPLEXITY_CONFIDENCE_FLOOR:
        if mode is PilotMode.SHADOW:
            log_action("complexity", mode, score, "no-op (shadow)", result)
        return None, False
    verdict = max(0, min(2, round(score)))
    if mode is PilotMode.SHADOW:
        log_action("complexity", mode, verdict, "no-op (shadow)", result)
        return None, False
    log_action("complexity", mode, verdict, f"scored tier={verdict}", result)
    return verdict, True


# ---------------------------------------------------------------------------
# 6.4 preflight_diff: the agent's pre-submit self-check (agent lane)
# ---------------------------------------------------------------------------


async def preflight_diff(
    session: AsyncSession,
    *,
    task_id: str,
    criteria: list[str],
    diff: str,
) -> dict | None:
    """Batched per-criterion + hygiene noul verdicts over the working diff.

    Returns a dict ``{"criteria": [{"criterion", "noul", "confidence",
    "addresses"}...], "hygiene": {"flagged", "noul", "confidence"}}`` or
    ``None`` when no verdict. Advisory by design: the envelope informs the
    agent, it never blocks or waives ``i_am_done`` (self-review exclusion,
    spec 5). In shadow mode the verdict is logged and ``None`` is returned:
    the baseline, so no envelope reaches the agent until the mode is ON.
    """
    # One noul per criterion, capped at 6, longest first (spec 6.4).
    ordered = sorted(criteria, key=len, reverse=True)[:6]
    questions: dict[str, DecisionQuestion] = {}
    for idx, criterion in enumerate(ordered):
        questions[f"criterion_{idx}"] = NoulQuestion(
            instructions=(
                f"The diff plausibly addresses this acceptance criterion: {criterion}"
            )
        )
    questions["hygiene"] = NoulQuestion(
        instructions=(
            "The diff contains debug leftovers, conflict markers, hardcoded "
            "secrets, or TODO stubs."
        )
    )
    state = {"task_id": task_id, "criteria": ordered, "diff": diff}
    mode, result = await decide_for_pilot(
        session, "preflight_diff", state, questions, session_id=f"preflight:{task_id}"
    )
    if result is None:
        return None
    criteria_verdicts = []
    for idx, criterion in enumerate(ordered):
        answer = result.answer(f"criterion_{idx}")
        if answer is None or answer.noul is None:
            continue
        criteria_verdicts.append(
            {
                "criterion": criterion,
                "addresses": answer.noul >= PREFLIGHT_ADVISORY_CONFIDENCE_FLOOR,
                "noul": answer.noul,
                "confidence": answer.confidence,
            }
        )
    hygiene_answer = result.answer("hygiene")
    hygiene = None
    if hygiene_answer is not None and hygiene_answer.noul is not None:
        hygiene = {
            "flagged": hygiene_answer.noul >= PREFLIGHT_ADVISORY_CONFIDENCE_FLOOR,
            "noul": hygiene_answer.noul,
            "confidence": hygiene_answer.confidence,
        }
    verdict_summary = {
        "criteria": len(criteria_verdicts),
        "hygiene_flagged": (hygiene or {}).get("flagged"),
    }
    if mode is PilotMode.SHADOW:
        # Shadow doctrine: verdicts logged, behavior unchanged. The envelope
        # never reaches the agent until the mode is ON.
        log_action("preflight_diff", mode, verdict_summary, "no-op (shadow)", result)
        return None
    log_action("preflight_diff", mode, verdict_summary, "advisory envelope", result)
    return {"criteria": criteria_verdicts, "hygiene": hygiene}


# ---------------------------------------------------------------------------
# 6.5 triage_failure: red-test triage (agent lane)
# ---------------------------------------------------------------------------


def _triage_verdict(choice: str | None, confidence: float | None) -> TriageLane:
    """The parsed lane, unknown unless the choice cleared the floor."""
    try:
        verdict = TriageLane(choice) if choice is not None else TriageLane.UNKNOWN
    except ValueError:
        verdict = TriageLane.UNKNOWN
    if (
        verdict is TriageLane.UNKNOWN
        or confidence is None
        or (confidence < TRIAGE_CONFIDENCE_FLOOR)
    ):
        verdict = TriageLane.UNKNOWN
    return verdict


async def triage_failure(
    session: AsyncSession,
    *,
    task_id: str,
    test_name: str,
    error_excerpt: str,
    changed_files_in_diff: list[str],
    is_retry: bool,
    recent_flake_history_for_test: list[str],
) -> TriageLane:
    """Classify a failed test: this dev's regression, a flake, or an
    environment failure. Below the confidence floor the envelope says
    ``unknown`` and the agent proceeds exactly as today (debug first). In
    shadow mode the verdict is logged and ``TriageLane.UNKNOWN`` is
    returned: the baseline, so no advisory lane reaches the agent until
    the mode is ON."""
    state = {
        "test_name": test_name,
        "error_excerpt": error_excerpt,
        "changed_files_in_diff": changed_files_in_diff,
        "is_retry": is_retry,
        "recent_flake_history_for_test": recent_flake_history_for_test,
    }
    questions = {
        "gate": ChoiceQuestion(
            instructions="Why is this test failing?",
            criteria={
                "my_regression": (
                    "Plausibly caused by this diff: the touched files or the "
                    "changed behavior connect to the failure."
                ),
                "flaky": (
                    "Known or previously-flaky test failing independent of the diff."
                ),
                "environment": (
                    "Runner/environment failure such as timeout, network, or "
                    "missing dependency."
                ),
            },
        ),
    }
    mode, result = await decide_for_pilot(
        session, "triage_failure", state, questions, session_id=f"triage:{task_id}"
    )
    if result is None:
        return TriageLane.UNKNOWN
    choice, confidence = _gate_choice(result)
    verdict = _triage_verdict(choice, confidence)
    if mode is PilotMode.SHADOW:
        # Shadow doctrine: verdicts logged, behavior unchanged. The advisory
        # lane never reaches the agent until the mode is ON.
        log_action("triage_failure", mode, verdict.value, "no-op (shadow)", result)
        return TriageLane.UNKNOWN
    log_action("triage_failure", mode, verdict.value, "advisory envelope", result)
    return verdict


# ---------------------------------------------------------------------------
# B28 transcript auto-notes (cognition lane, spec 7.1 row B28 / stage 4)
# ---------------------------------------------------------------------------


async def transcript_note_worthy(
    session: AsyncSession,
    *,
    task_id: str,
    segments: list[str],
) -> list[bool] | None:
    """One noul per transcript segment: is this a durable decision or
    constraint worth a journal entry? Returns a list aligned with
    ``segments`` (True = worth persisting) ONLY when the mode is ON;
    shadow and off return ``None`` exactly like every other pilot, so a
    caller that skips its own mode check can never write journal entries
    out of shadow data. Durable knowledge is otherwise captured only if
    the agent self-reported via `note`; this is the system-side capture
    pass at finalize. Advisory to the journal: it never edits or gates
    anything."""
    # Cap the batch: at most 20 segments per call, each head-capped.
    capped = [s[:1500] for s in segments[:20]]
    if not capped:
        return []
    questions = {
        f"seg_{idx}": NoulQuestion(
            instructions=(
                "This session segment contains a durable decision, "
                "constraint, or learning worth persisting to the agent's "
                "journal."
            )
        )
        for idx in range(len(capped))
    }
    mode, result = await decide_for_pilot(
        session,
        "transcript_notes",
        {"task_id": task_id, "segments": capped},
        questions,
        session_id=f"notes:{task_id}",
    )
    if result is None or mode is not PilotMode.ON:
        return None
    verdicts: list[bool] = []
    for idx in range(len(capped)):
        answer = result.answer(f"seg_{idx}")
        noul = answer.noul if answer else None
        verdicts.append(bool(noul is not None and noul >= TRANSCRIPT_NOTE_FLOOR))
    log_action(
        "transcript_notes",
        mode,
        f"{sum(verdicts)}/{len(verdicts)} worthy",
        "journal entries",
        result,
    )
    return verdicts


# ---------------------------------------------------------------------------
# 6.6 steer_gate: A2A steering (cognition lane, flagship)
# ---------------------------------------------------------------------------


def _steer_verdict(choice: str | None, confidence: float | None) -> SteerMode:
    """The parsed steering mode; steering needs confidence at/above 0.8."""
    try:
        verdict = (
            SteerMode(choice) if choice is not None else SteerMode.QUEUE_AFTER_CURRENT
        )
    except ValueError:
        verdict = SteerMode.QUEUE_AFTER_CURRENT
    steering = verdict in (
        SteerMode.STEER_SWITCH_CONSIDERATION,
        SteerMode.STEER_NOW,
    )
    if steering and (confidence is None or confidence < STEER_CONFIDENCE_FLOOR):
        verdict = SteerMode.QUEUE_AFTER_CURRENT
    return verdict


async def steer_gate(
    session: AsyncSession,
    *,
    message_id: str,
    sender: str,
    purpose: str | None,
    requires_response: bool,
    message_body: str,
    recipient_context: dict,
) -> SteerMode:
    """Classify HOW a peer DM should reach its recipient, against the
    recipient's actual work context. Steering modes need confidence >= 0.8;
    anything else (low confidence, no verdict, Jev down) is
    ``queue_after_current``: visible at the recipient's next read, never
    lost, exactly today's pull-only behavior. Steering injects context,
    never commands (spec 6.6 cognition doctrine)."""
    state = {
        "sender": sender,
        "purpose": purpose,
        "requires_response": requires_response,
        "message_body": message_body,
        "recipient_work_context": recipient_context,
    }
    questions = {
        "gate": ChoiceQuestion(
            instructions=(
                "How should this message reach the recipient, given their "
                "current work context?"
            ),
            criteria={
                "steer_switch_consideration": (
                    "The message changes direction materially enough that the "
                    "recipient should weigh switching tasks at their next "
                    "context boundary (the switch itself stays their/their "
                    "PM's lifecycle decision)."
                ),
                "steer_now": (
                    "The message changes what the recipient should do within "
                    "the CURRENT task (missing input, correction, blocker "
                    "lifted) and belongs in their next context boundary."
                ),
                "queue_after_current": (
                    "A real work item, but nothing about the current task "
                    "makes it urgent; deliver at the recipient's next natural "
                    "read."
                ),
                "fyi_pull": (
                    "Ordinary coordination chatter or status; pull-only, "
                    "badge counters unchanged."
                ),
            },
        ),
    }
    mode, result = await decide_for_pilot(
        session, "steer_gate", state, questions, session_id=f"steer:{message_id}"
    )
    if result is None:
        return SteerMode.QUEUE_AFTER_CURRENT
    choice, confidence = _gate_choice(result)
    verdict = _steer_verdict(choice, confidence)
    if mode is PilotMode.SHADOW:
        log_action("steer_gate", mode, verdict.value, "no-op (shadow)", result)
        return SteerMode.QUEUE_AFTER_CURRENT
    log_action("steer_gate", mode, verdict.value, f"mode={verdict.value}", result)
    return verdict


# ---------------------------------------------------------------------------
# B27 tool_spotlight: spawn-briefing verb highlight (cognition lane, spec
# 7.1 row B27 / stage 4)
# ---------------------------------------------------------------------------


# Input band (spec 10 limit 3: many-option choice degrades past ~20
# options) and output shape: fewer than 8 verbs is not worth a call,
# more than 20 is outside the model's reliable verdict range, and the
# highlight is always 5-7 verbs.
TOOL_SPOTLIGHT_MIN_VERBS = 8
TOOL_SPOTLIGHT_MAX_VERBS = 20
TOOL_SPOTLIGHT_MIN_HIGHLIGHTS = 5
TOOL_SPOTLIGHT_MAX_HIGHLIGHTS = 7
# Many-option choice is the model's weakest surface (Hummin's measured
# gray-zone operating point is 0.7; a 20-way pick does not get a
# discount).
TOOL_SPOTLIGHT_CONFIDENCE_FLOOR = 0.7


def _spotlight_ranking(probabilities: dict[str, float]) -> list[str] | None:
    """Rank the positively-scored verbs; None below the highlight minimum."""
    ranked = sorted(
        ((verb, p) for verb, p in probabilities.items() if p > 0.0),
        key=lambda item: item[1],
        reverse=True,
    )
    verdict = [verb for verb, _ in ranked[:TOOL_SPOTLIGHT_MAX_HIGHLIGHTS]]
    if len(verdict) < TOOL_SPOTLIGHT_MIN_HIGHLIGHTS:
        return None
    return verdict


def _spotlight_reject(mode: PilotMode, result: DecisionResult, line: str) -> None:
    """Log a rejected highlight in every non-OFF mode so the decision_log
    keeps the rejection cases the Auditor's aggregates read."""
    if mode is not PilotMode.OFF:
        log_action("tool_spotlight", mode, None, line, result)


async def tool_spotlight(
    session: AsyncSession,
    *,
    agent_slug: str,
    task_title: str,
    task_description: str,
    verbs: list[str],
) -> list[str] | None:
    """Pick which 5-7 verbs of the agent's per-role surface matter most for
    THIS task (B27: action-space navigation). Purely additive: the caller
    renders a highlight line into the spawn briefing and the full surface
    stays available no matter what this returns. ``None`` (below floor, out
    of the option band, no verdict, Jev down) means render nothing, exactly
    the pre-spotlight briefing."""
    if not (TOOL_SPOTLIGHT_MIN_VERBS <= len(verbs) <= TOOL_SPOTLIGHT_MAX_VERBS):
        return None
    questions = {
        "gate": ChoiceQuestion(
            instructions=(
                "Which of this agent's verbs matter most for the current "
                "task? Pick the ones this task should lean on first."
            ),
            criteria=dict.fromkeys(
                verbs, "This verb matters most for the current task."
            ),
        ),
    }
    state = {
        "agent_slug": agent_slug,
        "task_title": task_title,
        "task_description": task_description,
        "verbs": verbs,
    }
    mode, result = await decide_for_pilot(
        session,
        "tool_spotlight",
        state,
        questions,
        session_id=f"spotlight:{agent_slug}",
    )
    if result is None:
        return None
    answer = result.answer("gate")
    confidence = answer.confidence if answer else None
    probabilities = answer.probabilities if answer else {}
    if confidence is None or confidence < TOOL_SPOTLIGHT_CONFIDENCE_FLOOR:
        _spotlight_reject(mode, result, "below floor; no highlight")
        return None
    verdict = _spotlight_ranking(probabilities)
    if verdict is None:
        _spotlight_reject(mode, result, "too few ranked verbs; no highlight")
        return None
    if mode is PilotMode.SHADOW:
        log_action("tool_spotlight", mode, verdict, "no-op (shadow)", result)
        return None
    log_action(
        "tool_spotlight",
        mode,
        verdict,
        f"spotlight {len(verdict)} verbs",
        result,
    )
    return verdict
