"""The gateway-lane Decisions pilots (spec 7.1 Tier B rows B1, B20, B31,
B34, B39, B41, B43).

Same contract as ``pilots.py``: every function bakes the fail-open
fallback in, callers never see raw Jev types, and a ``None`` /
below-threshold / low-confidence verdict means "do exactly what you did
before this service existed". Shadow mode logs the verdict and the
action the pilot WOULD have taken, then behaves like ``off``. None of
these pilots gates a lifecycle transition: B1 can only tighten an
already-passed structural plan check, B20/B31/B43 are advisory envelope
hints, B34/B39 may only escalate (a notification or a ``next`` hint),
and B41 may only PRUNE already-retrieved memory.

Per-pilot modes live in the settings store as ``decisions.pilot.{slug}``
= off | shadow | on (rows already declared in the settings service),
resolved through the shared ``pilot_mode`` chokepoint.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from roboco.services.decisions.pilots import (
    PilotMode,
    decide_for_pilot,
    log_action,
)
from roboco.services.decisions.schemas import (
    ChoiceQuestion,
    DecisionAnswer,
    DecisionQuestion,
    DecisionResult,
    NoulQuestion,
    ScoreQuestion,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Thresholds (spec 7.1 rows + the wiring decisions). Calibrate from
# logged shadow data, never vibes.
PLAN_QUALITY_FLOOR = 0.7
COMMIT_INTENT_FLOOR = 0.7
FINDINGS_MAP_FLOOR = 0.6
FINDINGS_MAP_MAX_BATCH = 12
IDLE_LEGITIMACY_FLOOR = 0.7
BRANCH_STALENESS_FLOOR = 0.7
LESSON_PRUNE_KEEP_FLOOR = 0.4
LESSON_PRUNE_MAX_BATCH = 12
COHERENCE_SCAFFOLD_FLOOR = 0.7
# plan_quality's own sub-task cap: shared no more with the findings-map
# batch, so retuning one never silently moves the other.
PLAN_SUBTASKS_CAP = 12

# Advisory hint text for B20 (the exact wording the spec pins).
COMMIT_INTENT_HINT = "intent looks mismatched: re-read the diff"


class IdleLegitimacy(StrEnum):
    """Verdict for the idle-legitimacy choice (B34)."""

    LEGIT_WAIT = "legit-wait"
    LIKELY_DONE_SUBMIT_NOW = "likely-done-submit-now"
    LIKELY_STRANDED_ESCALATE = "likely-stranded-escalate"


def _answer(result: DecisionResult, key: str) -> DecisionAnswer | None:
    return result.answer(key)


def _score_confidence(
    result: DecisionResult, key: str
) -> tuple[float | None, float | None]:
    """(score, confidence) off one answer; both None without an answer."""
    answer = _answer(result, key)
    return (
        answer.score if answer else None,
        answer.confidence if answer else None,
    )


def _chosen_confidence(
    result: DecisionResult, key: str
) -> tuple[str | None, float | None]:
    """(choice, confidence) off one answer; both None without an answer."""
    answer = _answer(result, key)
    return (
        answer.choice if answer else None,
        answer.confidence if answer else None,
    )


def _clip(text: Any, cap: int = 2000) -> str:
    """Best-effort string coercion with a tail cap for state payloads."""
    if text is None:
        return ""
    return str(text)[:cap]


# ---------------------------------------------------------------------------
# B1 plan_quality: PM plan gate scoring lane
# ---------------------------------------------------------------------------


def _plan_quality_subtasks(
    sub_tasks: list[dict[str, Any]],
) -> list[dict[str, str | None]]:
    """Head-capped sub-task view for the state payload."""
    return [
        {
            "title": _clip(st.get("title") if isinstance(st, dict) else st),
            "description": _clip(
                st.get("description") if isinstance(st, dict) else None
            ),
        }
        for st in sub_tasks[:PLAN_SUBTASKS_CAP]
    ]


async def plan_quality(
    session: AsyncSession,
    *,
    task_id: str,
    task_title: str,
    approach: str,
    sub_tasks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Score a structurally-valid PM decomposition plan (B1).

    The structural gate (character/element counts) has already passed at
    the call site; garbage padded to the 150-char minimum is exactly the
    failure this scores. Returns ``{"inadequate", "score", "confidence"}``
    when ON and confident, ``None`` otherwise (shadow logs, below-floor,
    no verdict, Jev down). The caller may only use a LOW score to REJECT
    a plan the structural check accepted, never to accept one it
    rejected.
    """
    state = {
        "task_id": task_id,
        "task_title": _clip(task_title),
        "approach": _clip(approach, 4000),
        "sub_tasks": _plan_quality_subtasks(sub_tasks),
    }
    questions = {
        "gate": ScoreQuestion(
            instructions=(
                "Is this decomposition plan adequate, given its title, "
                "approach narrative, and sub-task list?"
            ),
            criteria=[
                "Inadequate: vague or padded prose, sub-tasks do not "
                "decompose the work into real, distinct steps",
                "Adequate: the approach explains HOW the work decomposes "
                "and every sub-task is a real, distinct step",
                "Strong: crisp decomposition, clear sequencing and "
                "ownership signals, no filler",
            ],
        ),
    }
    mode, result = await decide_for_pilot(
        session, "plan_quality", state, questions, session_id=f"planq:{task_id}"
    )
    if result is None:
        return None
    score, confidence = _score_confidence(result, "gate")
    if score is None or confidence is None or confidence < PLAN_QUALITY_FLOOR:
        if mode is PilotMode.SHADOW:
            log_action("plan_quality", mode, score, "no-op (shadow)", result)
        return None
    verdict = max(0, min(2, round(score)))
    inadequate = verdict == 0
    if mode is PilotMode.SHADOW:
        log_action("plan_quality", mode, verdict, "no-op (shadow)", result)
        return None
    log_action(
        "plan_quality",
        mode,
        verdict,
        "reject padded plan" if inadequate else "pass",
        result,
    )
    return {"inadequate": inadequate, "score": verdict, "confidence": confidence}


# ---------------------------------------------------------------------------
# B20 commit_intent: advisory mismatch hint in the commit validator
# ---------------------------------------------------------------------------


async def commit_intent_hint(
    session: AsyncSession,
    *,
    task_id: str,
    message: str,
    diff_summary: str,
) -> str | None:
    """Noul "the commit message matches the diff's intent" (B20).

    Returns the advisory hint text when ON and the model is confident the
    message does NOT match the diff (noul of "matches" at or below
    ``1 - COMMIT_INTENT_FLOOR`` mirrors the floor: the model is at least
    as confident about the mismatch as the floor demands about a match).
    Never bypasses or strengthens the shape check; below floor, shadow,
    off, or any failure returns ``None`` (no hint, exactly today).
    """
    state = {
        "task_id": task_id,
        "message": _clip(message),
        "diff_summary": _clip(diff_summary, 4000),
    }
    questions = {
        "gate": NoulQuestion(
            instructions=(
                "This commit message matches the intent of the diff it accompanies."
            )
        ),
    }
    mode, result = await decide_for_pilot(
        session, "commit_intent", state, questions, session_id=f"intent:{task_id}"
    )
    if result is None:
        return None
    answer = _answer(result, "gate")
    noul = answer.noul if answer else None
    mismatched = noul is not None and noul <= 1 - COMMIT_INTENT_FLOOR
    if mode is PilotMode.SHADOW:
        log_action("commit_intent", mode, noul, "no-op (shadow)", result)
        return None
    log_action(
        "commit_intent",
        mode,
        noul,
        "advisory hint" if mismatched else "no hint",
        result,
    )
    return COMMIT_INTENT_HINT if mismatched else None


# ---------------------------------------------------------------------------
# B31 findings_mapping: suggested finding-to-diff map for QA
# ---------------------------------------------------------------------------


def _file_overlap(changed_file: str, finding_file: str | None) -> bool:
    """Loose path-overlap test between a diff file and a finding's file."""
    if not finding_file:
        return False
    a = changed_file.replace("\\", "/").lower()
    b = finding_file.replace("\\", "/").lower()
    return a == b or a.endswith(b) or b.endswith(a) or b in a


def _overlap_files(finding: dict[str, Any], files_changed: list[str]) -> list[str]:
    """Diff files plausibly covering a finding's file, best-effort. A
    finding with no file never claims the whole diff (that would make
    every suggested map useless noise)."""
    target = finding.get("file")
    if not files_changed or not target:
        return []
    hits = [f for f in files_changed if _file_overlap(f, target)]
    return hits


def _findings_questions(ordered: list[dict[str, Any]]) -> dict[str, NoulQuestion]:
    """One noul question per ordered finding. The finding TEXT lives in
    the state's ``findings`` list (where the client's budget pass can
    trim it); the question carries only the index reference, so a large
    batch cannot smuggle unbounded text past the Laya context through
    the instructions."""
    return {
        f"finding_{idx}": NoulQuestion(
            instructions=(
                "The diff plausibly addresses finding number "
                f"{idx} from the state's findings list (file/line "
                "overlap plus semantics)."
            )
        )
        for idx in range(len(ordered))
    }


def _findings_state_rows(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Head-capped finding rows for the state payload (order preserved:
    question ``finding_{idx}`` refers to row ``idx``)."""
    return [
        {
            "idx": idx,
            "id": _clip(f.get("id"), 80),
            "file": _clip(f.get("file"), 200),
            "expected": _clip(f.get("expected"), 400),
            "actual": _clip(f.get("actual"), 400),
        }
        for idx, f in enumerate(ordered)
    ]


def _findings_map(
    result: DecisionResult,
    ordered: list[dict[str, Any]],
    files_changed: list[str],
) -> list[dict[str, Any]]:
    """Collect the at-or-above-floor suggested file maps per finding."""
    mapping: list[dict[str, Any]] = []
    for idx, f in enumerate(ordered):
        answer = result.answer(f"finding_{idx}")
        noul = answer.noul if answer else None
        if answer is None or noul is None or noul < FINDINGS_MAP_FLOOR:
            continue
        mapping.append(
            {
                "finding_id": str(f.get("id") or ""),
                "files": _overlap_files(f, list(files_changed)),
                "line": f.get("line"),
                "noul": noul,
                "confidence": answer.confidence,
            }
        )
    return mapping


async def findings_mapping(
    session: AsyncSession,
    *,
    task_id: str,
    findings: list[dict[str, Any]],
    diff: str,
    files_changed: list[str],
) -> list[dict[str, Any]] | None:
    """One batched noul per finding: "this diff plausibly addresses
    finding F (file:line overlap + semantics)" (B31).

    Returns a ``suggested_mapping`` (finding id -> plausible files +
    confidence) for findings at or above ``FINDINGS_MAP_FLOOR``, or
    ``None`` when there is no verdict (shadow behaves as off).
    ADVISORY ONLY: the caller never auto-closes, waives, or re-opens a
    finding on this output; the findings ledger semantics are untouched.
    """
    ordered = sorted(
        findings,
        key=lambda f: len(
            f"{f.get('file', '')}{f.get('expected', '')}{f.get('actual', '')}"
        ),
        reverse=True,
    )[:FINDINGS_MAP_MAX_BATCH]
    if not ordered:
        return []
    questions = _findings_questions(ordered)
    state = {
        "task_id": task_id,
        "findings": _findings_state_rows(ordered),
        "diff": _clip(diff, 20_000),
        "files_changed": list(files_changed)[: FINDINGS_MAP_MAX_BATCH * 4],
    }
    mode, result = await decide_for_pilot(
        session, "findings_mapping", state, questions, session_id=f"fmap:{task_id}"
    )
    if result is None:
        return None
    mapping = _findings_map(result, ordered, list(files_changed))
    log_action(
        "findings_mapping",
        mode,
        f"{len(mapping)}/{len(ordered)} plausible",
        "suggested map" if mode is PilotMode.ON else "no-op (shadow)",
        result,
    )
    if mode is PilotMode.SHADOW:
        return None
    return mapping


# ---------------------------------------------------------------------------
# B34 idle_legitimacy: is this idle legitimate or stranded work?
# ---------------------------------------------------------------------------


def _idle_state(owned_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Head-capped owned-task view for the state payload."""
    return [
        {
            "task_id": _clip(t.get("task_id")),
            "title": _clip(t.get("title"), 300),
            "status": _clip(t.get("status"), 60),
            "commit_count": t.get("commit_count"),
            "has_pr": bool(t.get("has_pr")),
            "minutes_since_last_activity": t.get("minutes_since_last_activity"),
        }
        for t in owned_tasks[:5]
    ]


def _idle_verdict(choice: str | None) -> IdleLegitimacy | None:
    """The parsed choice, or None when absent/unparseable."""
    if choice is None:
        return None
    try:
        return IdleLegitimacy(choice)
    except ValueError:
        return None


async def idle_legitimacy(
    session: AsyncSession,
    *,
    agent_slug: str,
    owned_tasks: list[dict[str, Any]],
) -> IdleLegitimacy | None:
    """Classify an i_am_idle against owned-task state (B34).

    Returns the verdict only when ON and confident; ``None`` means
    "today's behavior" (shadow logs then returns None). The caller may
    only ESCALATE on ``LIKELY_STRANDED_ESCALATE`` (a PM notification) or
    hint on ``LIKELY_DONE_SUBMIT_NOW`` (a ``next`` line); it NEVER blocks
    a legit idle.
    """
    state = {
        "agent_slug": agent_slug,
        "owned_tasks": _idle_state(owned_tasks),
    }
    questions = {
        "gate": ChoiceQuestion(
            instructions=(
                "This agent is about to idle. Is the idle legitimate given "
                "the state of the tasks it owns?"
            ),
            criteria={
                "legit-wait": (
                    "Waiting on an external dependency (review, QA, a "
                    "sibling task, an unblock) is the natural next step."
                ),
                "likely-done-submit-now": (
                    "The work looks finished (commits exist, nothing "
                    "blocking) but was never submitted; the agent should "
                    "submit (i_am_done / submit_up) before idling."
                ),
                "likely-stranded-escalate": (
                    "The task looks stranded: real work exists but no PR "
                    "and no submission path was taken, so the task would "
                    "rot until the reaper; a PM should be notified."
                ),
            },
        ),
    }
    mode, result = await decide_for_pilot(
        session,
        "idle_legitimacy",
        state,
        questions,
        session_id=f"idle:{agent_slug}",
    )
    if result is None:
        return None
    choice, confidence = _chosen_confidence(result, "gate")
    verdict = _idle_verdict(choice)
    if verdict is None or confidence is None or confidence < IDLE_LEGITIMACY_FLOOR:
        if mode is PilotMode.SHADOW:
            log_action("idle_legitimacy", mode, choice, "no-op (shadow)", result)
        return None
    if mode is PilotMode.SHADOW:
        log_action("idle_legitimacy", mode, verdict.value, "no-op (shadow)", result)
        return None
    log_action(
        "idle_legitimacy", mode, verdict.value, f"verdict={verdict.value}", result
    )
    return verdict


# ---------------------------------------------------------------------------
# B39 branch_staleness: mid-work behind-base early warning
# ---------------------------------------------------------------------------


def _staleness_answers(
    result: DecisionResult,
) -> tuple[float | None, float | None, float | None]:
    """(noul, confidence, risk_score) off the risk + severity answers."""
    risk_answer = result.answer("risk")
    sev_answer = result.answer("severity")
    return (
        risk_answer.noul if risk_answer else None,
        risk_answer.confidence if risk_answer else None,
        sev_answer.score if sev_answer else None,
    )


def _staleness_hit(noul: float | None, confidence: float | None) -> bool:
    """True when both the risk noul and its calibration clear the floor."""
    return (
        noul is not None
        and confidence is not None
        and noul >= BRANCH_STALENESS_FLOOR
        and confidence >= BRANCH_STALENESS_FLOOR
    )


def _staleness_payload(
    noul: float | None,
    confidence: float | None,
    risk_score: float | None,
) -> dict[str, Any]:
    """The advisory envelope the caller may notify the developer with."""
    return {
        "noul": noul,
        "risk_score": max(0, min(2, round(risk_score)))
        if risk_score is not None
        else None,
        "confidence": confidence,
    }


async def branch_staleness(
    session: AsyncSession,
    *,
    task_id: str,
    base_branch: str,
    behind: int,
    ahead: int,
    changed_files: list[str],
) -> dict[str, Any] | None:
    """Noul + score "base moved; conflict risk with new base commits" for
    an in-progress branch (B39).

    Returns ``{"noul", "risk_score", "confidence"}`` when ON and
    confident; the caller may ONLY add a notification to the dev, never
    block or auto-rebase. ``None`` (below floor, shadow, off, no verdict)
    means no notification.
    """
    state = {
        "task_id": task_id,
        "base_branch": base_branch,
        "behind": behind,
        "ahead": ahead,
        "changed_files": [str(f) for f in changed_files[:20]],
    }
    questions: dict[str, DecisionQuestion] = {
        "risk": NoulQuestion(
            instructions=(
                "The base branch has moved and the new base commits carry "
                "real conflict risk for this branch's in-flight work, worth "
                "telling the developer about mid-work instead of waiting "
                "for the submit gate."
            )
        ),
        "severity": ScoreQuestion(
            instructions="How severe is the likely conflict exposure?",
            criteria=[
                "Low: behind commits touch unrelated surfaces",
                "Moderate: some adjacency to this branch's files",
                "High: direct overlap or semantic coupling with this branch's files",
            ],
        ),
    }
    mode, result = await decide_for_pilot(
        session,
        "branch_staleness",
        state,
        questions,
        session_id=f"stale:{task_id}",
    )
    if result is None:
        return None
    noul, confidence, risk_score = _staleness_answers(result)
    confident_hit = _staleness_hit(noul, confidence)
    if mode is PilotMode.SHADOW:
        log_action("branch_staleness", mode, noul, "no-op (shadow)", result)
        return None
    log_action(
        "branch_staleness",
        mode,
        noul,
        "notify dev" if confident_hit else "no notification",
        result,
    )
    if not confident_hit:
        return None
    return _staleness_payload(noul, confidence, risk_score)


# ---------------------------------------------------------------------------
# B41 lesson_prune: applicability prune over injected institutional memory
# ---------------------------------------------------------------------------


def _lesson_text(lesson: Any) -> str:
    """Best-effort flat text rendering of one retrieved memory item."""
    if isinstance(lesson, dict):
        for key in ("content", "text", "summary", "lesson", "body", "title"):
            value = lesson.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return " ".join(str(v) for v in lesson.values())
    return str(lesson)


def _lesson_questions(capped: list[Any]) -> dict[str, ScoreQuestion]:
    """One applicability score question per capped lesson."""
    return {
        f"lesson_{idx}": ScoreQuestion(
            instructions=("How applicable is this stored lesson to the task at hand?"),
            criteria=[
                "Not applicable: unrelated surface, domain, or failure mode",
                "Partially applicable: adjacent domain but different context",
                "Directly applicable: same surface, domain, or failure mode",
            ],
        )
        for idx in range(len(capped))
    }


def _kept_lessons(result: DecisionResult, capped: list[Any]) -> tuple[list[Any], int]:
    """Split the batch into (kept, pruned) on the applicability floor.

    Normalized 0-1 applicability; an unreadable answer NEVER prunes
    (fail-open keeps the lesson) - only a readable score below the keep
    floor drops a lesson from the injected list."""
    kept: list[Any] = []
    pruned = 0
    for idx, lesson in enumerate(capped):
        answer = result.answer(f"lesson_{idx}")
        score = answer.score if answer else None
        if score is None:
            kept.append(lesson)
            continue
        if (score / 2) >= LESSON_PRUNE_KEEP_FLOOR:
            kept.append(lesson)
        else:
            pruned += 1
    return kept, pruned


async def lesson_prune(
    session: AsyncSession,
    *,
    task_id: str,
    lessons: list[Any],
    task_description: str,
    plan: str,
    files_touched: list[str],
) -> list[Any] | None:
    """Score each retrieved lesson's applicability against (description +
    plan + files-touched) and return the KEPT list (B41).

    Returns the pruned-to-keep list only when ON; ``None`` (off, shadow,
    no verdict) means the caller injects ALL lessons exactly as today.
    May only PRUNE SCORED lessons: lessons beyond the batch cap are
    passed through unscored (dropping them by index would subtract from
    the injected list without a verdict), and an unreadable answer keeps
    its lesson. The retrieval query, the floor, and the add path are
    untouched.
    """
    capped = lessons[:LESSON_PRUNE_MAX_BATCH]
    if not capped:
        return None
    texts = [_clip(_lesson_text(lesson), 1200) for lesson in capped]
    questions = _lesson_questions(capped)
    state = {
        "task_id": task_id,
        "task_description": _clip(task_description, 4000),
        "plan": _clip(plan, 4000),
        "files_touched": [str(f) for f in files_touched[:20]],
        "lessons": texts,
    }
    mode, result = await decide_for_pilot(
        session, "lesson_prune", state, questions, session_id=f"lprune:{task_id}"
    )
    if result is None:
        return None
    kept, pruned = _kept_lessons(result, capped)
    log_action(
        "lesson_prune",
        mode,
        f"pruned {pruned}/{len(capped)}",
        "prune injected list" if mode is PilotMode.ON else "no-op (shadow)",
        result,
    )
    if mode is PilotMode.SHADOW:
        return None
    return kept + lessons[LESSON_PRUNE_MAX_BATCH:]


# ---------------------------------------------------------------------------
# B43 assembled_coherence: scaffolding score for the assembled-PR gate
# ---------------------------------------------------------------------------


async def assembled_coherence(
    session: AsyncSession,
    *,
    task_id: str,
    diff: str,
    acceptance_criteria: list[str],
    files_changed: list[str],
) -> dict[str, Any] | None:
    """Score assembled-branch coherence (AC coverage x mixed concerns x
    missing pieces) for the gate reviewer (B43).

    Returns ``{"score", "confidence"}`` when ON and confident; the caller
    surfaces it in the gate claim envelope evidence as
    ``coherence_scaffold``. Purely advisory scaffolding for the existing
    reviewer: it never decides, blocks, or passes the gate. ``None``
    (below floor, shadow, off, no verdict) means the field is omitted.
    """
    state = {
        "task_id": task_id,
        "diff": _clip(diff, 20_000),
        "acceptance_criteria": [str(ac) for ac in acceptance_criteria[:12]],
        "files_changed": [str(f) for f in files_changed[:40]],
    }
    questions = {
        "gate": ScoreQuestion(
            instructions=(
                "How coherently does this assembled branch cover its "
                "acceptance criteria (coverage x mixed concerns x missing "
                "pieces)?"
            ),
            criteria=[
                "Incoherent: mixed unrelated concerns or large AC gaps",
                "Mostly coherent: minor gaps or mild scope mixing",
                "Coherent: the diff maps cleanly onto the AC list",
            ],
        ),
    }
    mode, result = await decide_for_pilot(
        session,
        "assembled_coherence",
        state,
        questions,
        session_id=f"coh:{task_id}",
    )
    if result is None:
        return None
    score, confidence = _score_confidence(result, "gate")
    if score is None or confidence is None or confidence < COHERENCE_SCAFFOLD_FLOOR:
        if mode is PilotMode.SHADOW:
            log_action("assembled_coherence", mode, score, "no-op (shadow)", result)
        return None
    verdict = max(0, min(2, round(score)))
    if mode is PilotMode.SHADOW:
        log_action("assembled_coherence", mode, verdict, "no-op (shadow)", result)
        return None
    log_action(
        "assembled_coherence",
        mode,
        verdict,
        "coherence_scaffold evidence",
        result,
    )
    return {"score": verdict, "confidence": confidence}
