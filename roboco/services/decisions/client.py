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

import contextlib
import copy
import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from typing import Any

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

# Stage-2 budget, Laya tier only: the default-serving Laya checkpoint
# (convaiinnovations/laya, typed-decisions subfolder) is a 1024-token
# ModernBERT with head_max_len=256, leaving roughly 768 tokens for state +
# question text. This budget's 1800 chars serialize to roughly 450-600
# tokens, fitting under that ceiling with headroom for the render
# template. Anything still large after the per-key caps silently
# truncates to mush inside the model, so the total budget is enforced
# deterministically here. The per-key caps above stay stage 1 for BOTH
# tiers (they are the billing guard for the OpenRouter fallback); this
# stage-2 pass runs after them, laya only, and is charged for the
# question text too (same context). When the questions alone exhaust the
# budget, the state keeps the floor below so a few short fields still
# mean something instead of arriving as pure mush. CAVEAT: the budget is
# calibrated to the typed-decisions checkpoint; repointing
# settings.decisions_model at the repo-root english checkpoint (512-token
# context, head 192 - the overlong-truncation failure the Hummin gate
# hit) requires halving this constant.
_LAYA_STATE_BUDGET_CHARS = 1_800
_LAYA_MIN_STATE_BUDGET_CHARS = 240

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


def _iter_string_slots(node: object) -> Iterator[tuple[Any, Any, str]]:
    """Yield ``(container, key, value)`` for every string reachable in the
    structure, so the budget pass can replace values in place."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                yield node, key, value
            else:
                yield from _iter_string_slots(value)
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            if isinstance(item, str):
                yield node, idx, item
            else:
                yield from _iter_string_slots(item)


# Minimal truncated form ("...[truncated]"): a string at or below this
# length cannot be usefully shrunk by the budget pass, so it is skipped.
_BUDGET_BARE_SUFFIX = "...[truncated]"
_BUDGET_MIN_SHRINKABLE_CHARS = len(_BUDGET_BARE_SUFFIX)

# Tail truncation keeps the informative END (same style as cap_text's
# keep-tail form, minus its room=0 edge case, which could grow a string).
_BUDGET_TAIL_PREFIX = "...[truncated] "
_BUDGET_HEAD_SUFFIX = " ...[truncated]"


def cap_state_to_budget(state: object, budget: int) -> object:
    """Stage-2 total-budget cap (Laya tier only): while the serialized state
    exceeds ``budget`` chars, truncate the currently-longest string value
    (re-walking each pass is fine at these sizes) until under budget or
    nothing shrinkable remains. Truncation keeps the "...[truncated]" style
    and the keep-tail semantics for tail keys. Every pass strictly shortens
    the chosen string, so the loop always terminates. Returns a new
    structure; the input is never mutated."""
    capped = copy.deepcopy(state)
    while True:
        serialized = json.dumps(capped, ensure_ascii=False, default=str)
        if len(serialized) <= budget:
            return capped
        shrinkable = [
            slot
            for slot in _iter_string_slots(capped)
            if len(slot[2]) > _BUDGET_MIN_SHRINKABLE_CHARS
        ]
        if not shrinkable:
            return capped
        container, key, value = max(shrinkable, key=lambda slot: len(slot[2]))
        # Room for the kept content: the overshoot plus the suffix chars
        # themselves (the truncated form re-adds a 15-char marker).
        room = max(
            len(value) - (len(serialized) - budget) - len(_BUDGET_HEAD_SUFFIX), 0
        )
        if _keeps_tail(str(key)):
            truncated = (
                _BUDGET_TAIL_PREFIX + value[-room:] if room else _BUDGET_BARE_SUFFIX
            )
        else:
            truncated = (
                value[:room] + _BUDGET_HEAD_SUFFIX if room else _BUDGET_BARE_SUFFIX
            )
        if truncated == value:  # nothing shrinkable remains
            return capped
        container[key] = truncated


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
    _pooled: httpx.AsyncClient | None = field(default=None, repr=False)
    _pooled_loop: object | None = field(default=None, repr=False)

    def _transport(self) -> httpx.AsyncClient:
        """The injected client (tests own its lifecycle) or one pooled
        client created lazily on first use and reused for the process
        lifetime: a fresh client per call paid a TCP connect (and a TLS
        handshake on the fallback tier) on every decision. If the running
        loop changed since the pool was created (tests relooping), the
        stale pool is dropped rather than reused bound to a dead loop."""
        import asyncio

        if self.http_client is not None:
            return self.http_client
        loop = asyncio.get_running_loop()
        if self._pooled is not None and self._pooled_loop is not loop:
            self._pooled = None
        if self._pooled is None:
            self._pooled = httpx.AsyncClient(
                limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60.0)
            )
            self._pooled_loop = loop
        return self._pooled

    async def aclose(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()
        if self._pooled is not None:
            pooled, self._pooled = self._pooled, None
            with contextlib.suppress(httpx.HTTPError):
                await pooled.aclose()

    async def decide(
        self,
        endpoint: DecisionsEndpoint,
        state: object,
        questions: Mapping[str, DecisionQuestion],
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

        questions_payload = {
            key: q.model_dump(exclude_none=True) for key, q in questions.items()
        }

        # Two cap stages (spec 3.1): the per-key caps run first for BOTH
        # tiers - they are the billing guard for the OpenRouter fallback.
        # The Laya tier additionally gets the deterministic total-budget
        # pass, because its tiny model context turns any state still large
        # after stage 1 into mush before a single verdict is rendered. The
        # budget accounts for the QUESTION text too (it rides in the same
        # ~1024-token context): the state gets whatever is left after the
        # serialized questions, so a wide batch shrinks the state instead
        # of silently overflowing the model. Questions are never truncated
        # themselves (a half instruction can invert a verdict); the floor
        # just keeps a few short fields alive when the questions alone
        # exhaust the budget.
        capped_state = cap_state(state)
        if endpoint.tier == "laya":
            question_chars = len(
                json.dumps(questions_payload, ensure_ascii=False, default=str)
            )
            state_budget = max(
                _LAYA_STATE_BUDGET_CHARS - question_chars,
                _LAYA_MIN_STATE_BUDGET_CHARS,
            )
            capped_state = cap_state_to_budget(capped_state, state_budget)
        state_json = json.dumps(capped_state, ensure_ascii=False, default=str)

        body = {
            "model": endpoint.model,
            "state": capped_state,
            "questions": questions_payload,
            "session_id": session_id[:256],
        }
        headers = {}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"

        started = time.monotonic()
        response = await self._post(endpoint, body, headers, circuit, session_id)
        if response is None:
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
            state_chars=len(state_json),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    async def _post(
        self,
        endpoint: DecisionsEndpoint,
        body: dict,
        headers: dict[str, str],
        circuit: _Circuit,
        session_id: str,
    ) -> httpx.Response | None:
        """POST one decisions batch. ``None`` = transport failure (the circuit
        is charged and the fail-open warning is logged here)."""
        try:
            client = self._transport()
            return await client.post(
                f"{endpoint.base_url.rstrip('/')}/api/alpha/decisions",
                json=body,
                headers=headers,
                timeout=endpoint.timeout_s,
            )
        except (httpx.HTTPError, OSError) as exc:
            circuit.record_failure()
            logger.warning(
                "decisions call failed; fail-open",
                tier=endpoint.tier,
                session_id=session_id,
                error=str(exc),
            )
            return None

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
