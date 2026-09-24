"""The content-lane Decisions pilots, one function per integration point
(spec 7.1 Tier B rows B3, B4, B10, B11, B18, B21, B22, B23, B24, B26).

Same contract as ``pilots.py``: every pilot bakes the fail-open fallback
in, callers never see raw Jev types, and a ``None``/below-threshold/
low-confidence verdict means "do exactly what you did before this
service existed". Shadow mode logs the verdict and the action the pilot
WOULD have taken, then behaves like ``off``. Directional doctrine per
row is enforced here, not at the call sites:

* B11/B22 may only SKIP work (never invent tasks or delete entries).
* B21 may only ADD delivery (never suppress a live-session delivery).
* B26 may only RELAX the char floor for substantive notes (never reject
  a long one).
* B18 only FILLS a kind/assignee the caller left unset or could not
  resolve; an explicit human choice is never overridden.

Per-pilot modes live in the settings store as ``decisions.pilot.{slug}``
= off | shadow | on, resolved through the single chokepoint
``pilots.pilot_mode`` (this module adds no settings rows of its own).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog

from roboco.services.decisions.pilots import (
    PilotMode,
    decide_for_pilot,
    log_action,
)
from roboco.services.decisions.schemas import (
    ChoiceQuestion,
    DecisionQuestion,
    DecisionResult,
    NoulQuestion,
    ScoreQuestion,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Thresholds. Same calibration doctrine as pilots.py: tight where a false
# positive skips work or spawns a session, looser where every lane is
# already legal. Calibrate from logged shadow data, never vibes.
# ---------------------------------------------------------------------------
INTAKE_PREROUTE_CONFIDENCE_FLOOR = 0.7  # spec 7.1 row B3
X_MENTION_TRIAGE_CONFIDENCE_FLOOR = 0.7
SEGMENT_CLASSIFY_CONFIDENCE_FLOOR = 0.7
VAULT_PREFILTER_NOUL_FLOOR = 0.8  # skipping a note is the costly direction
SECRETARY_NL_CONFIDENCE_FLOOR = 0.7  # spec 7.1 row B18
TG_FREETEXT_NOUL_FLOOR = 0.75  # additive delivery still deserves a bar
MEMORY_DISTILL_NOUL_FLOOR = 0.8  # skipping a lesson is the costly direction
CHANGELOG_HIGHLIGHTS_CONFIDENCE_FLOOR = 0.7
PROACTIVE_DOMAIN_CONFIDENCE_FLOOR = 0.7
DECISION_NOTE_SUFFICIENCY_CONFIDENCE_FLOOR = 0.7
# B23: fewer than this many parsed highlights is not worth a pick call.
_MIN_PICKABLE_HIGHLIGHTS = 2

# Input band (spec 10 limit 3: many-option choice degrades past ~20
# options), mirroring pilots.TOOL_SPOTLIGHT_*.
_MAX_CHOICE_OPTIONS = 20
_MAX_BATCHED_QUESTIONS = 20
_STATE_TEXT_CAP = 4000


class IntakeRoute(StrEnum):
    """Verdict for the intake pre-routing choice (B3)."""

    INTAKE_CHAT = "intake_chat"
    SECRETARY_DIRECTIVE = "secretary_directive"
    QUICK_ANSWER = "quick_answer"
    NOISE = "noise"


class MentionTriage(StrEnum):
    """Verdict for the X mention triage choice (B4)."""

    REPLY_WORTHY_QUESTION = "reply_worthy_question"
    BUG_REPORT = "bug_report"
    COMPLIMENT = "compliment"
    SPAM = "spam"


# Verdicts that SUPPRESS the local-LLM draft call (B4's whole point: not
# every mention deserves a draft). Questions and bug reports proceed.
X_MENTION_SUPPRESS_VERDICTS = frozenset({MentionTriage.SPAM, MentionTriage.COMPLIMENT})


@dataclass(frozen=True)
class VaultPrefilterVerdict:
    """B11: ``skip`` is the only actuated half (confidently not worth a
    task skips the extraction call); ``route`` is advisory shadow data."""

    skip: bool
    route: str | None


# The extraction segment vocabulary (B10) - the same six MessageType
# values the regex lists and the TOON fallback prompt use.
SEGMENT_TYPES = (
    "reasoning",
    "dialogue",
    "decision",
    "action",
    "blocker",
    "technical",
)


def _cap(text: str | None) -> str:
    """Head-cap one free-text state field (client.py re-caps per key, but
    the band here keeps the requests predictable)."""
    value = str(text or "")
    return value[:_STATE_TEXT_CAP]


async def _with_session(session: Any, run: Any) -> Any:
    """Run ``run(session)``, opening a short-lived background session when
    the caller has none (extraction, memory_distiller, and proactive are
    session-less services). Read-only use: nothing is written, so the
    context's commit is a no-op."""
    if session is not None:
        return await run(session)
    from roboco.db.base import get_db_context

    async with get_db_context() as db:
        return await run(db)


# ---------------------------------------------------------------------------
# Shared gate cores: one ChoiceQuestion / NoulQuestion / ScoreQuestion,
# with the confidence floor and shadow semantics handled in one place so
# every content pilot behaves identically.
# ---------------------------------------------------------------------------


async def _ask_choice(
    session: Any,
    *,
    pilot: str,
    state: dict,
    instructions: str,
    criteria: dict[str, str],
    session_id: str,
    floor: float,
) -> tuple[PilotMode, str | None, DecisionResult | None]:
    """One confident-or-Nothing choice gate. Returns ``(mode, choice,
    result)``; ``choice`` is None when off/unreachable/no verdict/below
    floor/in shadow (shadow logs the would-be choice first)."""
    mode, result = await decide_for_pilot(
        session,
        pilot,
        state,
        {"gate": ChoiceQuestion(instructions=instructions, criteria=criteria)},
        session_id=session_id,
    )
    if result is None:
        return mode, None, None
    answer = result.answer("gate")
    choice = answer.choice if answer else None
    confidence = answer.confidence if answer else None
    if choice is None or confidence is None or confidence < floor:
        log_action(pilot, mode, choice, "below floor; baseline behavior kept", result)
        return mode, None, result
    if mode is PilotMode.SHADOW:
        log_action(pilot, mode, choice, "no-op (shadow)", result)
        return mode, None, result
    return mode, choice, result


async def _ask_noul(
    session: Any,
    *,
    pilot: str,
    state: dict,
    instructions: str,
    session_id: str,
) -> tuple[PilotMode, float | None, DecisionResult | None]:
    """One raw noul gate (no floor applied here: the direction - worthy vs
    not-worthy - is per-row and handled by the caller)."""
    mode, result = await decide_for_pilot(
        session,
        pilot,
        state,
        {"gate": NoulQuestion(instructions=instructions)},
        session_id=session_id,
    )
    if result is None:
        return mode, None, None
    answer = result.answer("gate")
    return mode, (answer.noul if answer else None), result


def _confidently_not(noul: float | None, floor: float) -> bool:
    """True when the statement is confidently FALSE: the noul for the
    positive statement sits at least ``floor`` below 1.0."""
    return noul is not None and (1.0 - noul) >= floor


def _capped_options(mapping: dict[str, str]) -> dict[str, str]:
    """Guard the option band: more than 20 options degrades the verdict,
    so an oversized vocabulary fails open instead."""
    if len(mapping) > _MAX_CHOICE_OPTIONS:
        return {}
    return mapping


# ---------------------------------------------------------------------------
# B3 intake_preroute: route unrouted CEO free text (telegram_inbound) and
# advise on live-intake user turns (prompter).
# ---------------------------------------------------------------------------


async def intake_preroute(session: Any, *, text: str, ref: str) -> IntakeRoute | None:
    """Classify one free-text CEO utterance: which intake-adjacent flow
    (if any) it belongs to. Below the floor / off / shadow the caller
    keeps today's behavior (drop the text, or let the live interview
    continue untouched). A ``quick_answer`` verdict routes to nothing by
    design: no cheap local-LLM reply machinery exists on these paths, so
    it behaves like ``noise`` with its own audit line."""
    state = {"text": _cap(text)}
    mode, choice, result = await _ask_choice(
        session,
        pilot="intake_preroute",
        state=state,
        instructions=(
            "Which flow does this CEO utterance belong in, given that it "
            "arrived outside any live chat session?"
        ),
        criteria={
            "intake_chat": (
                "The utterance describes work to plan and delegate: it "
                "warrants a full intake interview that drafts a task."
            ),
            "secretary_directive": (
                "The utterance is a directive to the chief-of-staff: read "
                "company state, relay a message, or act on existing tasks."
            ),
            "quick_answer": (
                "A one-line factual answer would settle it; no agent "
                "session is warranted."
            ),
            "noise": (
                "Small talk, a stray fragment, or nothing actionable; "
                "suppress entirely."
            ),
        },
        session_id=f"intake:{ref}",
        floor=INTAKE_PREROUTE_CONFIDENCE_FLOOR,
    )
    if choice is None:
        return None
    try:
        verdict = IntakeRoute(choice)
    except ValueError:
        return None
    action = (
        "route to intake (/newtask flow)"
        if verdict is IntakeRoute.INTAKE_CHAT
        else "route to secretary session"
        if verdict is IntakeRoute.SECRETARY_DIRECTIVE
        else "no quick-answer machinery; treated as suppressed"
        if verdict is IntakeRoute.QUICK_ANSWER
        else "suppressed"
    )
    log_action("intake_preroute", mode, verdict.value, action, result)
    return verdict


# ---------------------------------------------------------------------------
# B4 x_mention_triage: not every meaningful mention deserves a draft call.
# ---------------------------------------------------------------------------


async def x_mention_triage(
    session: Any, *, mention_id: str, text: str
) -> MentionTriage | None:
    """Classify one engagement-passing mention. A confident ``spam`` or
    ``compliment`` verdict suppresses the local-LLM draft call (the
    caller leaves the mention unmarked-seen, mirroring the engagement
    floor skip); question/bug-report verdicts and everything below the
    floor proceed exactly as today."""
    state = {"mention_id": mention_id, "text": _cap(text)}
    mode, choice, result = await _ask_choice(
        session,
        pilot="x_mention_triage",
        state=state,
        instructions="What kind of X mention is this?",
        criteria={
            "reply_worthy_question": (
                "A question about the product or company a public reply should answer."
            ),
            "bug_report": (
                "A defect or failure report worth acknowledging and triaging publicly."
            ),
            "compliment": (
                "Praise or gratitude with no question inside; no draft warranted."
            ),
            "spam": ("Bot noise, promotion, or engagement bait; no draft warranted."),
        },
        session_id=f"triage:{mention_id}",
        floor=X_MENTION_TRIAGE_CONFIDENCE_FLOOR,
    )
    if choice is None:
        return None
    try:
        verdict = MentionTriage(choice)
    except ValueError:
        return None
    log_action(
        "x_mention_triage",
        mode,
        verdict.value,
        "suppress draft"
        if verdict in X_MENTION_SUPPRESS_VERDICTS
        else "proceed to draft",
        result,
    )
    return verdict


# ---------------------------------------------------------------------------
# B10 segment_classify: the session segment classifier that replaces both
# the regex lists and the expensive full-LLM fallback when confident.
# ---------------------------------------------------------------------------


def _segment_criteria() -> dict[str, str]:
    return {
        "reasoning": "Deliberation: thinking out loud, analysis, plans.",
        "dialogue": "Addressed to someone: questions, requests, mentions.",
        "decision": ("A settled choice: decided, selected, going with X."),
        "action": ("A performed or in-flight act: creating, running, completed."),
        "blocker": ("Something prevents progress: blocked, error, waiting on."),
        "technical": (
            "Code or system detail: code blocks, APIs, schemas, errors' stacks."
        ),
    }


def _segment_questions(capped: list[str]) -> dict[str, DecisionQuestion]:
    """One type-choice question per capped segment."""
    return {
        f"seg_{idx}": ChoiceQuestion(
            instructions="What type of message is this segment?",
            criteria=_segment_criteria(),
        )
        for idx in range(len(capped))
    }


def _segment_verdicts(
    result: DecisionResult, capped: list[str]
) -> tuple[list[tuple[str, float] | None], int]:
    """(verdicts, confident count) over the capped segment batch."""
    verdicts: list[tuple[str, float] | None] = []
    confident = 0
    for idx in range(len(capped)):
        answer = result.answer(f"seg_{idx}")
        choice = answer.choice if answer else None
        confidence = answer.confidence if answer else None
        if (
            choice in SEGMENT_TYPES
            and confidence is not None
            and confidence >= SEGMENT_CLASSIFY_CONFIDENCE_FLOOR
        ):
            confident += 1
            verdicts.append((str(choice), confidence))
        else:
            verdicts.append(None)
    return verdicts, confident


def _segment_action(mode: PilotMode, confident: int, total: int) -> str:
    """The audit action line for the batch."""
    return (
        (
            "replace regex, skip full-LLM fallback"
            if confident == total
            else "partial; regex + fallback as today"
        )
        if mode is PilotMode.ON
        else "no-op (shadow)"
    )


async def segment_classify(
    session: Any, *, segments: list[str]
) -> list[tuple[str, float] | None]:
    """Batched segment classification: one ChoiceQuestion per segment,
    one Decisions request for the whole buffer. Returns a list aligned
    with ``segments``; a ``None`` entry (or a ``None`` list) means that
    segment keeps today's regex result. The caller skips its full-LLM
    fallback only when every segment came back confident."""
    if not segments:
        return []
    capped = [s[:1500] for s in segments[:_MAX_BATCHED_QUESTIONS]]
    questions = _segment_questions(capped)
    state = {"segments": capped}
    mode, result = await decide_for_pilot(
        session,
        "segment_classify",
        state,
        questions,
        session_id=f"segments:{len(capped)}",
    )
    if result is None:
        return []
    verdicts, confident = _segment_verdicts(result, capped)
    log_action(
        "segment_classify",
        mode,
        f"{confident}/{len(capped)} confident",
        _segment_action(mode, confident, len(capped)),
        result,
    )
    if mode is not PilotMode.ON:
        return []
    return verdicts


# ---------------------------------------------------------------------------
# B11 vault_prefilter: confidently not-worthy notes skip the extraction
# call (may only SKIP work, never invent tasks).
# ---------------------------------------------------------------------------


async def vault_prefilter(
    session: Any, *, note_path: str, body: str
) -> VaultPrefilterVerdict | None:
    """Gate the per-note local-LLM extraction call: one noul ("warrants a
    task") batched with a small routing choice. ``skip`` is True only when
    the note is CONFIDENTLY not worth a task (noul at least ``floor``
    below 1.0); anything else - and the whole pilot when off/shadow -
    means run the extraction exactly as today. The routing choice is
    advisory shadow data and never creates anything."""
    state = {"note_path": note_path, "body": _cap(body)}
    route_criteria = _capped_options(
        {
            "intake_draft": "A task draft for board review, as today.",
            "bug_report": "A defect worth recording.",
            "improvement_idea": "An enhancement or polish idea.",
            "reference_only": ("Reference material; nothing actionable on its own."),
        }
    )
    questions: dict[str, DecisionQuestion] = {
        "gate": NoulQuestion(
            instructions=(
                "This vault note warrants a task: it describes concrete "
                "work, a defect, or a decision that should enter the "
                "company's workflow."
            )
        ),
        "route": ChoiceQuestion(
            instructions="If the note warrants anything, what is it?",
            criteria=route_criteria or {"intake_draft": "As today."},
        ),
    }
    mode, result = await decide_for_pilot(
        session,
        "vault_prefilter",
        state,
        questions,
        session_id=f"vault:{note_path}",
    )
    if result is None:
        return None
    gate = result.answer("gate")
    route_answer = result.answer("route")
    noul = gate.noul if gate else None
    skip = _confidently_not(noul, VAULT_PREFILTER_NOUL_FLOOR) and (mode is PilotMode.ON)
    log_action(
        "vault_prefilter",
        mode,
        (f"skip={skip} route={route_answer.choice if route_answer else None}"),
        "skip extraction call" if skip else "extraction call as today",
        result,
    )
    if mode is not PilotMode.ON:
        return None
    return VaultPrefilterVerdict(
        skip=skip, route=route_answer.choice if route_answer else None
    )


# ---------------------------------------------------------------------------
# B18 secretary_nl: directive kind + assignee from natural language. Fills
# only what the caller left unset or the string resolver could not match;
# an explicit human choice is never overridden.
# ---------------------------------------------------------------------------


_DIRECTIVE_KIND_CRITERIA = {
    "relay_message": "Post a CEO-dictated message to specific agents.",
    "update_charter": "Edit the company charter / goals.",
    "control_task": "Start, cancel, override, or edit a task.",
    "approve_pitch": "Approve a pitch (provision + spend).",
    "announce": "Broadcast a company-wide announcement.",
}


async def secretary_directive_kind(session: Any, *, utterance: str) -> str | None:
    """Resolve a natural-language directive to a ``DirectiveKind`` value.
    Returns None (caller keeps today's requirement of an explicit kind)
    when off/shadow/unconfident. Called only when the caller left the
    kind unset, so an explicit panel-picked kind is never overridden."""
    state = {"utterance": _cap(utterance)}
    _mode, choice, result = await _ask_choice(
        session,
        pilot="secretary_nl",
        state=state,
        instructions="Which directive kind does this utterance ask for?",
        criteria=_DIRECTIVE_KIND_CRITERIA,
        session_id="secretary:kind",
        floor=SECRETARY_NL_CONFIDENCE_FLOOR,
    )
    if choice is None:
        return None
    log_action("secretary_nl", PilotMode.ON, choice, "fill directive kind", result)
    return choice


async def secretary_assignee(
    session: Any, *, utterance: str, candidate_slugs: list[str]
) -> str | None:
    """Resolve a natural-language assignee to an agent slug from the
    roster. Called only where today's exact slug/UUID match FAILED, so it
    can only fill a match, never override one. Out-of-band rosters (0 or
    more than 20 agents) fail open."""
    criteria = _capped_options(
        dict.fromkeys(
            sorted(slug for slug in candidate_slugs if slug),
            "This agent is the one the utterance refers to.",
        )
    )
    if not criteria:
        return None
    state = {"utterance": _cap(utterance), "agents": sorted(criteria)}
    _mode, choice, result = await _ask_choice(
        session,
        pilot="secretary_nl",
        state=state,
        instructions="Which agent does this utterance assign the work to?",
        criteria=criteria,
        session_id="secretary:assignee",
        floor=SECRETARY_NL_CONFIDENCE_FLOOR,
    )
    if choice is None:
        return None
    log_action("secretary_nl", PilotMode.ON, choice, "fill assignee slug", result)
    return choice


# ---------------------------------------------------------------------------
# B21 tg_freetext_gate: MAY ONLY ADD delivery of otherwise-ignored free
# text, never suppress a live-session delivery.
# ---------------------------------------------------------------------------


async def tg_freetext_gate(session: Any, *, chat_id: str, text: str) -> bool:
    """True only when the pilot is ON and confidently says this free text
    deserves a session or an answer even though no bridge session is live
    (the caller then surfaces it; nothing is ever suppressed). Off/shadow/
    below-floor all return False, which is exactly today's silent drop."""
    state = {"chat_id": chat_id, "text": _cap(text)}
    mode, noul, result = await _ask_noul(
        session,
        pilot="tg_freetext_gate",
        state=state,
        instructions=(
            "This free-text message deserves a session or an answer: it "
            "carries a real request, question, or directive, not chatter."
        ),
        session_id=f"tggate:{chat_id}",
    )
    deserves = noul is not None and noul >= TG_FREETEXT_NOUL_FLOOR
    if mode is PilotMode.SHADOW:
        log_action(
            "tg_freetext_gate",
            mode,
            noul,
            "no-op (shadow)",
            result,
        )
        return False
    log_action(
        "tg_freetext_gate",
        mode,
        noul,
        "surface to CEO" if deserves else "drop as today",
        result,
    )
    return bool(deserves and mode is PilotMode.ON)


# ---------------------------------------------------------------------------
# B22 memory_distill_gate: confidently not-worthy completions skip the
# distill + persist (may only SKIP; existing entries are never touched).
# ---------------------------------------------------------------------------


async def memory_distill_gate(
    session: Any,
    *,
    title: str,
    acceptance_criteria: list[str],
    dev_notes: str | None,
    qa_notes: str | None,
    commit_messages: list[str],
) -> bool:
    """True only when the completed task is CONFIDENTLY not worth a
    persisted lesson (the caller then skips the distill call and the
    persist exactly as when the distiller returns None). Off/shadow/
    below-floor all return False: distill + persist as today. Session may
    be None (the distiller is session-less)."""
    state = {
        "title": _cap(title),
        "acceptance_criteria": [str(c)[:500] for c in acceptance_criteria][:20],
        "dev_notes": _cap(dev_notes),
        "qa_notes": _cap(qa_notes),
        "commit_messages": [str(c)[:200] for c in commit_messages][:20],
    }

    async def _run(db: Any) -> bool:
        mode, noul, result = await _ask_noul(
            db,
            pilot="memory_distill_gate",
            state=state,
            instructions=(
                "This completed task holds a durable, reusable lesson "
                "(a real problem, approach, or gotcha) worth persisting "
                "to org memory."
            ),
            session_id="distill",
        )
        skip = _confidently_not(noul, MEMORY_DISTILL_NOUL_FLOOR) and (
            mode is PilotMode.ON
        )
        if mode is PilotMode.SHADOW:
            log_action("memory_distill_gate", mode, noul, "no-op (shadow)", result)
            return False
        log_action(
            "memory_distill_gate",
            mode,
            noul,
            "skip distill + persist" if skip else "distill + persist as today",
            result,
        )
        return skip

    try:
        return bool(await _with_session(session, _run))
    except Exception as exc:
        logger.warning(
            "memory_distill_gate failed to open a session; keeping the "
            "distill path (fail-open)",
            error=str(exc),
        )
        return False


# ---------------------------------------------------------------------------
# B23 changelog_highlights: pick the top highlight worth amplifying; both
# the X release post and the release video consume this one function.
# ---------------------------------------------------------------------------


async def changelog_highlights_pick(
    session: Any,
    *,
    version: str,
    product_name: str | None,
    highlights: list[str],
) -> list[str]:
    """Reorder the parsed changelog highlights so the entry worth
    amplifying leads (today the first bullet leads by regex accident).
    Returns the input order unchanged when off/shadow/below-floor/unusable
    (fewer than 2 highlights, or over the option band) - the exact
    first-bullet behavior."""
    candidates = {
        str(idx): f"Lead the announcement with this: {h[:300]}"
        for idx, h in enumerate(highlights[:_MAX_CHOICE_OPTIONS])
    }
    if len(candidates) < _MIN_PICKABLE_HIGHLIGHTS:
        return highlights
    state = {
        "version": version,
        "product_name": product_name,
        "highlights": [h[:300] for h in highlights[:_MAX_CHOICE_OPTIONS]],
    }
    mode, choice, result = await _ask_choice(
        session,
        pilot="changelog_highlights",
        state=state,
        instructions=(
            "Which single highlight is the most user-visible, "
            "announcement-worthy lead for this release?"
        ),
        criteria=candidates,
        session_id=f"changelog:{version}",
        floor=CHANGELOG_HIGHLIGHTS_CONFIDENCE_FLOOR,
    )
    if choice is None:
        return highlights
    try:
        idx = int(choice)
    except (TypeError, ValueError):
        return highlights
    if not 0 <= idx < len(highlights):
        return highlights
    ordered = [highlights[idx], *highlights[:idx], *highlights[idx + 1 :]]
    log_action(
        "changelog_highlights",
        mode,
        highlights[idx][:120],
        "picked top highlight",
        result,
    )
    return ordered


# ---------------------------------------------------------------------------
# B24 proactive_domain: override the keyword/role map when confident.
# ---------------------------------------------------------------------------


DOMAINS = ("security", "workflow", "coding")


async def proactive_domain(
    session: Any,
    *,
    task_type: str | None,
    description: str,
    keyword_domain: str,
) -> str | None:
    """Classify the standards domain for a claimed task. A confident
    verdict overrides the keyword map's guess; anything else returns None
    and the caller keeps ``keyword_domain``."""
    state = {
        "task_type": task_type,
        "description": _cap(description),
        "keyword_guess": keyword_domain,
    }
    _mode, choice, result = await _ask_choice(
        session,
        pilot="proactive_domain",
        state=state,
        instructions="Which standards domain does this task belong to?",
        criteria={
            "security": "Auth, secrets, encryption, attack surface.",
            "workflow": "Process, board mechanics, coordination, QA flow.",
            "coding": "Ordinary feature/bug code work.",
        },
        session_id="domain",
        floor=PROACTIVE_DOMAIN_CONFIDENCE_FLOOR,
    )
    if choice is None:
        return None
    if choice not in DOMAINS:
        return None
    log_action(
        "proactive_domain", PilotMode.ON, choice, "override keyword guess", result
    )
    return choice


# ---------------------------------------------------------------------------
# B26 decision_note_sufficiency: a substantive note may relax the char
# floor; a long note is never rejected.
# ---------------------------------------------------------------------------

# The 0-2 legend: 0 = trivial ("ok", "no"), 1 = short but specific, 2 =
# fully substantive (named facts, reasons, next steps) even if brief.
DECISION_NOTE_SUBSTANTIVE_SCORE = 2.0


async def decision_note_sufficiency(session: Any, *, kind: str, reason: str) -> bool:
    """True only when the pilot is ON and confidently scores this
    below-min-chars rejection note a 2 (fully substantive). The caller
    then accepts it despite the char floor. Callers only consult this for
    notes BELOW the floor, so a long note can never be rejected by it."""
    state = {"kind": kind, "reason": _cap(reason)}
    mode, result = await decide_for_pilot(
        session,
        "decision_note_sufficiency",
        state,
        {
            "gate": ScoreQuestion(
                instructions=(
                    "How substantive is this decision/rejection note as "
                    "an audit-trail record?"
                ),
                criteria=[
                    "Trivial: no information beyond sentiment.",
                    "Short but specific: names one concrete fact.",
                    (
                        "Substantive: names concrete facts, reasons, or "
                        "next steps even though it is brief."
                    ),
                ],
            )
        },
        session_id=f"note:{kind}",
    )
    if result is None:
        return False
    answer = result.answer("gate")
    score = answer.score if answer else None
    confidence = answer.confidence if answer else None
    substantive = (
        score is not None
        and round(score) == int(DECISION_NOTE_SUBSTANTIVE_SCORE)
        and confidence is not None
        and confidence >= DECISION_NOTE_SUFFICIENCY_CONFIDENCE_FLOOR
    )
    if mode is PilotMode.SHADOW:
        log_action("decision_note_sufficiency", mode, score, "no-op (shadow)", result)
        return False
    log_action(
        "decision_note_sufficiency",
        mode,
        score,
        "accept below-min note" if substantive else "char floor stands",
        result,
    )
    return bool(substantive and mode is PilotMode.ON)
