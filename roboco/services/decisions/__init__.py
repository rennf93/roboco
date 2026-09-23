"""The Decisions service: typed System One decisions for RoboCo.

A small orchestrator-side service that answers TYPED questions (choice /
score / noul) with calibrated confidence at decision points that would
otherwise be a full LLM call or a hardcoded heuristic. TypeSafe's "System
One" pattern (Jev): state in, typed answers with probabilities out, and the
calling code accepts, rejects, or escalates on a confidence threshold -
"AI multiple choice, not AI essay writing."

Two tiers behind one wire format, resolved per call (``resolver.py``):

1. Laya (BUILT-IN default): the self-hosted ``roboco-jev`` sidecar
   (``settings.decisions_base_url``), no key, no spend, no egress.
2. OpenRouter (OPT-IN fallback): the Decisions API with
   ``typesafe/jev-1.13``, serving only when the CEO opts in AND the AI
   Provider screen has a key.

Tier 3 is the floor: master flag off, or no healthy tier, means every call
site does exactly what it did before this package existed. Decisions can
only add behavior, never subtract; nothing materializes on a verdict alone.

Doctrine (docs/internal/jev-decisions-spec.md sections 5-6): fail-open
always (the single fail-CLOSED exception is a future injection screen),
one attempt no retries, every verdict logged with its evidence, shadow
mode before live mode per pilot, and no Jev verdict may satisfy, skip, or
auto-pass any lifecycle gate.
"""

from roboco.services.decisions.client import DecisionsClient, DecisionsEndpoint
from roboco.services.decisions.pilots import (
    PILOT_SLUGS,
    ParkingLane,
    PilotMode,
    SelfHealGate,
    SteerMode,
    TriageLane,
    complexity_score,
    decide_for_pilot,
    parking_route,
    pilot_mode,
    preflight_diff,
    self_heal_transient,
    steer_gate,
    transcript_note_worthy,
    triage_failure,
)
from roboco.services.decisions.schemas import (
    ChoiceQuestion,
    DecisionAnswer,
    DecisionQuestion,
    DecisionResult,
    NoulQuestion,
    ScoreQuestion,
)

__all__ = [
    "PILOT_SLUGS",
    "ChoiceQuestion",
    "DecisionAnswer",
    "DecisionQuestion",
    "DecisionResult",
    "DecisionsClient",
    "DecisionsEndpoint",
    "NoulQuestion",
    "ParkingLane",
    "PilotMode",
    "ScoreQuestion",
    "SelfHealGate",
    "SteerMode",
    "TriageLane",
    "complexity_score",
    "decide_for_pilot",
    "parking_route",
    "pilot_mode",
    "preflight_diff",
    "self_heal_transient",
    "steer_gate",
    "transcript_note_worthy",
    "triage_failure",
]
