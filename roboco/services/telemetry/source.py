"""Telemetry ingestion for production self-healing ("engine 4").

RoboCo heals ITSELF: this reads a health signal for RoboCo's OWN repo — the
single project named by ``settings.self_heal_project_slug`` — and normalizes it
into ``TelemetrySample``s the regression detector can assess. The sample
contract is the only thing the detector depends on, so the source is swappable:
a GitHub Actions CI source today, another CI/APM source later, with no change to
the engine.

Read-only and repo-singular by design — it watches RoboCo's own project, never
other/client repos (the agent org's general, repo-agnostic delivery work is a
separate concern). ``fetch`` returns no samples (and never raises) when self-heal
has no target or the signal is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from roboco.config import settings
from roboco.logging import get_logger
from roboco.services.git import GitService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


# GitHub Actions run conclusions that count as a regression signal. ``cancelled``
# / ``neutral`` / ``skipped`` / ``action_required`` are deliberately excluded —
# they are not a failing build.
FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})


@dataclass(frozen=True)
class TelemetrySample:
    """One normalized health reading for RoboCo's own repo.

    ``value >= threshold`` is a breach (a regression). For CI: ``value`` is 1.0
    when the latest completed run failed and 0.0 when it passed; ``threshold`` is
    1.0. The string fields carry enough to describe and link the signal.
    """

    signal_name: str
    value: float
    threshold: float
    window: str
    repo_hint: str  # the self-heal project slug (RoboCo's own repo)
    observed_at: str
    raw_ref: str  # a link to the underlying evidence (e.g. the CI run URL)
    detail: str = ""

    @property
    def is_breach(self) -> bool:
        """True when the reading breaches its threshold (a regression)."""
        return self.value >= self.threshold


@runtime_checkable
class TelemetrySource(Protocol):
    """Pull-based, read-only health source. ``fetch`` never raises into the loop."""

    async def fetch(self) -> list[TelemetrySample]: ...


class GitHubCITelemetrySource:
    """CI health for RoboCo's own repo, from GitHub Actions run conclusions.

    Watches ONLY ``settings.self_heal_project_slug`` (RoboCo healing itself): the
    latest completed run on that project's default branch. A failing run yields a
    breaching sample, a passing run a non-breaching one; no target / no run / any
    error yields no samples.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fetch(self) -> list[TelemetrySample]:
        slug = settings.self_heal_project_slug.strip()
        if not slug:
            return []
        workflow = settings.self_heal_ci_workflow.strip() or None
        ci = await GitService(self.session).get_latest_ci_conclusion(
            slug, workflow=workflow
        )
        if ci is None:
            # No signal is NOT the same as "CI is green": an armed self-heal that
            # silently reads nothing (no/expired token, a non-default branch
            # filter, or a GitHub error) would never fire and never explain why.
            # Make that loud so it is diagnosable instead of an invisible no-op.
            logger.warning(
                "self-heal: no CI signal — cannot detect regressions",
                project_slug=slug,
                workflow=workflow,
                hint=(
                    "check the project's git token is valid, its default_branch "
                    "matches the repo, and GitHub is reachable"
                ),
            )
            return []
        conclusion = (ci.get("conclusion") or "").lower()
        failed = conclusion in FAILURE_CONCLUSIONS
        run_name = ci.get("run_name") or ""
        detail = f"CI on {slug}@{ci.get('branch')} concluded '{conclusion}'"
        if run_name:
            detail += f" ({run_name})"
        return [
            TelemetrySample(
                signal_name=f"ci_conclusion:{slug}",
                value=1.0 if failed else 0.0,
                threshold=1.0,
                window="latest_completed_run",
                repo_hint=slug,
                observed_at=str(ci.get("completed_at") or ""),
                raw_ref=str(ci.get("run_url") or ""),
                detail=detail,
            )
        ]


def get_ci_telemetry_source(session: AsyncSession) -> GitHubCITelemetrySource:
    """Construct the GitHub-CI telemetry source bound to ``session``."""
    return GitHubCITelemetrySource(session)
