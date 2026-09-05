"""In-path PR-gate wiring for the cross-vendor second-review pass.

Bridges ``roboco.services.second_review`` (the sibling unit's pure
risk-threshold classifier + DB-backed provider resolver) into ``pr_pass``:
when a task qualifies, resolves a second-review provider, optionally runs a
pluggable check over the assembled diff, and reports the outcome in a shape
the gate can stamp into its envelope and insert into the existing
``task_review_findings`` ledger under a dedicated origin.

Kept out of ``pr_gate.py`` for the same reason ``findings.py`` /
``collision.py`` are split out: a focused, independently testable unit
instead of one more concern piled onto an already-large mixin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.second_review import get_second_review_service, task_is_high_stakes

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from uuid import UUID

    from roboco.foundation.policy.content import Finding
    from roboco.models.base import ModelProvider
    from roboco.models.task import Task

    # Runs the second pass over the assembled diff, returning any findings
    # the first-line gate missed. A plain ``Callable`` alias rather than a
    # ``Protocol`` — an async ``Protocol.__call__`` runs into a known mypy
    # structural-matching gap against plain async functions.
    SecondReviewRunner = Callable[[Task, str, ModelProvider], Awaitable[list[Finding]]]

# Dedicated ledger origin for this pass — no new table/surface, per the
# task's constraint; the existing per-finding rendering + Findings tab
# already handle any string origin.
SECOND_REVIEW_ORIGIN = "second_review"


async def _no_op_runner(
    _task: Task, _diff: str, _provider: ModelProvider
) -> list[Finding]:
    """Default runner — raises no findings.

    ponytail: none of grok/gemini/openai/kimi expose a synchronous
    completion API from this codebase (they run as CLI-subscription agent
    runtimes mounted per-container, not callable HTTP clients —
    ``roboco/llm/providers/*``); only Anthropic has a direct API key
    (``settings.anthropic_api_key``). A genuine cross-vendor synchronous
    review client is separate, larger work than this gate-wiring task.
    The wiring below is real and fully exercised — inject a ``runner`` that
    returns findings (as a real client will, once one exists) to run the
    second pass for real; production runs this no-op until then.
    """
    return []


@dataclass(frozen=True)
class SecondReviewOutcome:
    """What the gate should record about a second-review attempt.

    ``applicable=False`` means the task doesn't qualify (flag off, or below
    the risk threshold) — the caller must add NOTHING to the gate's
    envelope/verdict in that case, so flag-off output stays byte-for-byte
    unchanged.
    """

    applicable: bool
    skipped: bool = False
    provider: ModelProvider | None = None
    skip_reason: str | None = None
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @classmethod
    def not_applicable(cls) -> SecondReviewOutcome:
        return cls(applicable=False)

    @classmethod
    def skip(cls, reason: str) -> SecondReviewOutcome:
        return cls(applicable=True, skipped=True, skip_reason=reason)

    @classmethod
    def ran(
        cls, provider: ModelProvider, findings: Sequence[Finding]
    ) -> SecondReviewOutcome:
        return cls(
            applicable=True, skipped=False, provider=provider, findings=tuple(findings)
        )

    def as_evidence(self) -> dict[str, Any] | None:
        """Envelope-facing payload — ``None`` when not applicable at all."""
        if not self.applicable:
            return None
        if self.skipped:
            return {"second_review": {"ran": False, "skip_reason": self.skip_reason}}
        provider_value = self.provider.value if self.provider is not None else None
        return {
            "second_review": {
                "ran": True,
                "provider": provider_value,
                "findings_count": len(self.findings),
            }
        }


async def run_second_review_for_gate(
    session: Any,
    task: Task,
    diff: str,
    *,
    authoring_provider: ModelProvider,
    runner: SecondReviewRunner | None = None,
) -> SecondReviewOutcome:
    """Resolve + (maybe) run the cross-vendor second-review pass for ``task``.

    Returns ``SecondReviewOutcome.not_applicable()`` when the task doesn't
    qualify (flag off or below the risk threshold — ``task_is_high_stakes``
    already folds both checks together). Otherwise resolves a differing
    enabled provider via the sibling ``SecondReviewService``; an explicit
    resolver skip (single provider enabled fleet-wide) is returned as-is and
    never blocks the caller. When a provider resolves, runs ``runner``
    (defaulting to ``_no_op_runner``) over the diff and carries its findings
    back for the caller to insert into the ledger.
    """
    if not task_is_high_stakes(task):
        return SecondReviewOutcome.not_applicable()
    selection = await get_second_review_service(session).resolve_second_reviewer(
        authoring_provider
    )
    if selection.skipped or selection.provider is None:
        return SecondReviewOutcome.skip(
            selection.skip_reason or "second review skipped"
        )
    active_runner = runner or _no_op_runner
    found = await active_runner(task, diff, selection.provider)
    return SecondReviewOutcome.ran(selection.provider, found)


async def insert_second_review_findings(
    session: Any,
    *,
    task_id: UUID,
    round: int,
    author_slug: str | None,
    findings: Sequence[Finding],
) -> list[Any]:
    """Insert the second pass's findings into the existing ledger under the
    dedicated ``second_review`` origin. A no-op when there is nothing to
    insert."""
    if not findings:
        return []
    rows, _summary = await findings_lib.insert_and_render(
        session,
        task_id=task_id,
        origin=SECOND_REVIEW_ORIGIN,
        round=round,
        author_slug=author_slug,
        findings=list(findings),
    )
    return rows
