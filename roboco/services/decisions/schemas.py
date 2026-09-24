"""Pydantic models mirroring the OpenRouter Decisions wire format.

This is NOT the OpenAI-compatible chat endpoint: the Decisions API takes a
``state`` plus a map of typed ``questions`` and returns typed ``answers``
(choice / score / noul) with calibrated confidence. Both tiers (the
self-hosted roboco-decisions sidecar and the OpenRouter fallback) speak this exact
shape, so one client serves both. Parsing is deliberately lenient: the
endpoint is Alpha-tagged on OpenRouter's side, so unknown answer fields are
ignored and a missing ``confidence`` surfaces as ``None`` (every caller
treats ``None`` as below-threshold, which is the fail-open direction).

See docs/internal/decisions-spec.md sections 3 and 4.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NoulQuestion(BaseModel):
    """A yes/no statement question. The answer's ``noul`` float is the
    probability the statement holds for the given state."""

    type: Literal["noul"] = "noul"
    instructions: str


class ChoiceQuestion(BaseModel):
    """A single pick from named ``criteria`` options."""

    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str]


class ScoreQuestion(BaseModel):
    """An ordinal score over a ``criteria`` legend (index = score)."""

    type: Literal["score"] = "score"
    instructions: str
    criteria: list[str]


DecisionQuestion = NoulQuestion | ChoiceQuestion | ScoreQuestion


class DecisionUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None


class DecisionAnswer(BaseModel):
    """One typed answer, already flattened for gating code.

    ``confidence`` is ``None`` when the payload omitted it (fail-open: every
    threshold gate treats ``None`` as below-threshold). Unknown wire fields
    are dropped here but preserved in ``raw_payload`` for logs.
    """

    model_config = ConfigDict(extra="ignore")

    key: str
    type: str
    noul: float | None = None
    choice: str | None = None
    score: float | None = None
    confidence: float | None = None
    probabilities: dict[str, float] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    def verdict(self) -> str | float | None:
        """The typed payload of this answer, whatever shape it arrived in."""
        if self.type == "noul":
            return self.noul
        if self.type == "choice":
            return self.choice
        if self.type == "score":
            return self.score
        return None


class DecisionResult(BaseModel):
    """A parsed Decisions API response plus the metadata the audit trail
    needs (active tier, session id, full raw payload for logs)."""

    answers: dict[str, DecisionAnswer]
    model: str | None = None
    usage: DecisionUsage = Field(default_factory=DecisionUsage)
    tier: str
    session_id: str
    raw: dict[str, Any] = Field(default_factory=dict)

    def answer(self, key: str) -> DecisionAnswer | None:
        return self.answers.get(key)


def _parse_answer(key: str, body: dict[str, Any]) -> DecisionAnswer:
    """Leniently flatten one answer entry (malformed fields stay None)."""
    return DecisionAnswer(
        key=str(key),
        type=str(body.get("type", "unknown")),
        noul=_as_float(body.get("noul")),
        choice=(str(body["choice"]) if body.get("choice") is not None else None),
        score=_as_float(body.get("score")),
        confidence=_as_float(body.get("confidence")),
        probabilities={
            str(k): float(v)
            for k, v in (body.get("probabilities") or {}).items()
            if isinstance(v, (int, float))
        },
        raw_payload=body,
    )


def _parse_usage(payload: dict[str, Any]) -> DecisionUsage:
    """Leniently parse the usage block (non-dict shapes stay empty)."""
    usage_raw = payload.get("usage") or {}
    return DecisionUsage(
        input_tokens=usage_raw.get("input_tokens")
        if isinstance(usage_raw, dict)
        else None,
        output_tokens=usage_raw.get("output_tokens")
        if isinstance(usage_raw, dict)
        else None,
        cost=_as_float(usage_raw.get("cost")) if isinstance(usage_raw, dict) else None,
    )


def parse_decisions_payload(
    payload: dict[str, Any], *, tier: str, session_id: str
) -> DecisionResult:
    """Leniently parse a Decisions API response body.

    Unknown answer keys are ignored; a malformed answer entry is skipped
    rather than failing the whole result. Raises only if the body is not a
    dict-shaped response at all.
    """
    answers_raw = payload.get("answers")
    answers: dict[str, DecisionAnswer] = {}
    if isinstance(answers_raw, dict):
        for key, body in answers_raw.items():
            if not isinstance(body, dict):
                continue
            answers[str(key)] = _parse_answer(str(key), body)
    return DecisionResult(
        answers=answers,
        model=str(payload["model"]) if payload.get("model") is not None else None,
        usage=_parse_usage(payload),
        tier=tier,
        session_id=session_id,
        raw=payload,
    )


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
