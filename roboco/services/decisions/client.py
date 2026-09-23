"""The one Decisions HTTP client, serving both tiers.

``roboco-decisions`` (the self-hosted sidecar) mirrors the OpenRouter Decisions
wire shape at ``/api/alpha/decisions``, so a single ``DecisionsClient``
covers the Laya tier (no auth, internal bridge) and the OpenRouter fallback
(bearer key, alpha endpoint). The resolver (``resolver.py``) picks the
endpoint per call; this module only knows how to ask and parse.

Doctrine (spec sections 4-5): one attempt, no retries, fail-open. ``None``
from ``decide`` means "no verdict" (disabled, timeout, HTTP error,
unparseable payload, circuit open) and every caller falls back to exactly
its pre-Decisions behavior. A small in-process circuit breaker keeps an
unhealthy backend from eating the sweep loop; it is a nice-to-have, not
load-bearing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache

import httpx
import structlog

from roboco.services.decisions.schemas import (
    DecisionQuestion,
    DecisionResult,
    parse_decisions_payload,
)
from roboco.services.decisions.spend_guard import record_402, record_spend

logger = structlog.get_logger(__name__)

# Client-side token caps (spec 3.1): EVERY state carries one, enforced here,
# so no call site can surprise-bill the fallback tier. Diffs 8k, error
# excerpts 2k, task descriptions 4k; anything else defaults to 4k. States
# carry structured JSON, so capping walks the dict.
_DIFF_CAP_CHARS = 8_000
_EXCERPT_CAP_CHARS = 2_000
_DESC_CAP_CHARS = 4_000
_DEFAULT_CAP_CHARS = 4_000

# Keys whose informative content sits at the END of the text (CI logs,
# tracebacks): keep the tail. Everything else keeps the head.
_TAIL_KEEP_KEY_PARTS = ("excerpt", "error", "log", "traceback", "body")

# Circuit breaker (spec 4): 3 consecutive failures open the circuit for
# 5 minutes, per tier, per process. In-memory only.
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_OPEN_SECONDS = 300.0


@dataclass
class DecisionsEndpoint:
    """A concrete Decisions backend the resolver selected for one call."""

    tier: str  # "laya" | "openrouter"
    base_url: str
    model: str
    timeout_s: float
    api_key: str | None = None


def cap_text(text: str, cap_chars: int, *, keep_tail: bool = False) -> str:
    """Truncate ``text`` to ``cap_chars`` keeping the informative side."""
    if len(text) <= cap_chars:
        return text
    suffix = " ...[truncated]" if not keep_tail else "...[truncated] "
    room = max(cap_chars - len(suffix), 0)
    if keep_tail:
        return suffix + text[-room:]
    return text[:room] + suffix


def cap_state(state: object) -> object:
    """Enforce the client-side token caps on a structured state, walking
    dicts/lists recursively. Long strings truncate per their key name."""
    if isinstance(state, dict):
        capped: dict = {}
        for key, value in state.items():
            if isinstance(value, str):
                capped[key] = cap_text(
                    value, _cap_for_key(str(key)), keep_tail=_keeps_tail(str(key))
                )
            else:
                capped[key] = cap_state(value)
        return capped
    if isinstance(state, list):
        return [cap_state(item) for item in state]
    return state


def _cap_for_key(key: str) -> int:
    lowered = key.lower()
    if "diff" in lowered:
        return _DIFF_CAP_CHARS
    if any(part in lowered for part in ("excerpt", "error", "log", "body")):
        return _EXCERPT_CAP_CHARS
    if "description" in lowered:
        return _DESC_CAP_CHARS
    return _DEFAULT_CAP_CHARS


def _keeps_tail(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _TAIL_KEEP_KEY_PARTS)


@dataclass
class _Circuit:
    consecutive_failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        # Half-open: past the window, let one attempt through to probe
        # recovery.
        return time.monotonic() - self.opened_at >= _CIRCUIT_OPEN_SECONDS

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= _CIRCUIT_FAILURE_THRESHOLD:
            self.opened_at = time.monotonic()


@dataclass
class DecisionsClient:
    """One implementation, two settings (base_url + api_key) per tier."""

    http_client: httpx.AsyncClient | None = None
    _circuits: dict[str, _Circuit] = field(default_factory=dict, repr=False)

    async def aclose(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()

    async def decide(
        self,
        endpoint: DecisionsEndpoint,
        state: object,
        questions: dict[str, DecisionQuestion],
        session_id: str,
    ) -> DecisionResult | None:
        """Ask one batched Decisions question set. ``None`` = no verdict
        (fail-open signal); the caller does exactly what it did before."""
        circuit = self._circuits.setdefault(endpoint.tier, _Circuit())
        if not circuit.allow():
            logger.debug(
                "decisions circuit open; skipping call",
                tier=endpoint.tier,
                session_id=session_id,
            )
            return None

        body = {
            "model": endpoint.model,
            "state": cap_state(state),
            "questions": {
                key: q.model_dump(exclude_none=True) for key, q in questions.items()
            },
            "session_id": session_id[:256],
        }
        headers = {}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"

        started = time.monotonic()
        try:
            client = self.http_client or httpx.AsyncClient()
            try:
                response = await client.post(
                    f"{endpoint.base_url.rstrip('/')}/api/alpha/decisions",
                    json=body,
                    headers=headers,
                    timeout=endpoint.timeout_s,
                )
            finally:
                if self.http_client is None:
                    await client.aclose()
        except (httpx.HTTPError, OSError) as exc:
            circuit.record_failure()
            logger.warning(
                "decisions call failed; fail-open",
                tier=endpoint.tier,
                session_id=session_id,
                error=str(exc),
            )
            return None

        payload = self._validated_payload(response, circuit, endpoint, session_id)
        if payload is None:
            return None

        circuit.record_success()
        result = parse_decisions_payload(
            payload, tier=endpoint.tier, session_id=session_id
        )
        # Observe-only spend tracking (spec 3.1): usage.cost is summed per
        # tier per UTC day and alerts once the daily threshold is crossed.
        # Never affects the verdict or the fail-open behavior.
        record_spend(result.tier, result.usage.cost)
        logger.info(
            "decision rendered",
            tier=result.tier,
            session_id=result.session_id,
            model=result.model,
            answers={key: ans.verdict() for key, ans in result.answers.items()},
            confidence={key: ans.confidence for key, ans in result.answers.items()},
            cost=result.usage.cost,
            input_tokens=result.usage.input_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    def _validated_payload(
        self,
        response: httpx.Response,
        circuit: _Circuit,
        endpoint: DecisionsEndpoint,
        session_id: str,
    ) -> dict | None:
        """Reject anything that is not a well-formed Decisions body. Every
        rejection counts a circuit failure and logs once, fail-open."""
        if response.status_code != httpx.codes.OK:
            circuit.record_failure()
            # A 402 (payment required / credits exhausted) is observed-only
            # spend-guard input (spec 3.1); two within an hour alert the CEO.
            # The fail-open behavior below is unchanged.
            if response.status_code == httpx.codes.PAYMENT_REQUIRED:
                record_402(endpoint.tier)
            logger.warning(
                "decisions backend returned an error; fail-open",
                tier=endpoint.tier,
                session_id=session_id,
                status_code=response.status_code,
                body_excerpt=cap_text(response.text, 500, keep_tail=True),
            )
            return None
        try:
            payload = response.json()
        except ValueError:
            circuit.record_failure()
            logger.warning(
                "decisions backend returned a non-JSON body; fail-open",
                tier=endpoint.tier,
                session_id=session_id,
            )
            return None
        if not isinstance(payload, dict):
            circuit.record_failure()
            logger.warning(
                "decisions backend returned an unexpected payload shape; fail-open",
                tier=endpoint.tier,
                session_id=session_id,
            )
            return None
        # An error envelope inside a 200 (OpenRouter does this under load):
        # still a no-verdict, still fail-open, no retry.
        if isinstance(payload.get("error"), dict):
            circuit.record_failure()
            logger.warning(
                "decisions backend returned an error envelope; fail-open",
                tier=endpoint.tier,
                session_id=session_id,
                error=payload["error"],
            )
            return None
        return payload


@lru_cache(maxsize=1)
def get_decisions_client() -> DecisionsClient:
    """Process-wide client (the circuit breaker is per-process, in-memory)."""
    return DecisionsClient()
