"""CI run history (spec 12.1 Wave 3): persist what pull-based telemetry
observes so outcome graders can see a run SEQUENCE, not just the latest
reading.

Two consumers write here - the ci-watch engine (per watched project, every
sweep) and the self-heal engine (its own repo) - by handing their already-
fetched telemetry samples to :func:`record_runs` immediately after the
fetch, before any of their own writes. Rows deduplicate on
(project_slug, workflow, observed_at_str): consecutive sweeps re-observe
the same latest run and cost one conflict-nothing insert. The trajectory
labeler prunes history past its retention window; its graders read
post-decision run sequences to grade ci_watch_route urgency and
dep_update_risk.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from roboco.db.tables import CiRunTable

logger = structlog.get_logger(__name__)

# A reading without a parseable completion stamp dedupes under this key.
_UNKNOWN_STAMP = ""

_RETENTION_DAYS = 45

_DEDUP_ELEMENTS = ("project_slug", "workflow", "observed_at_str")


def parse_observed_at(raw: str) -> datetime | None:
    """Best-effort ISO parse of a sample's completion stamp; naive stamps
    read as UTC, garbage as None (the row keeps the verbatim string)."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _rows_from_samples(
    samples: list[Any], workflow: str | None
) -> list[dict[str, Any]]:
    """Normalized rows from one sweep's readings. The conclusion is the
    normalized breach flag (failure/success), not the detail sentence."""
    rows: list[dict[str, Any]] = []
    for sample in samples:
        observed = str(getattr(sample, "observed_at", "") or "")
        breach = bool(getattr(sample, "is_breach", False))
        slug = str(getattr(sample, "repo_hint", "") or "")
        if not slug:
            continue
        rows.append(
            {
                "project_slug": slug[:200],
                "workflow": (workflow or "")[:200] or None,
                "conclusion": "failure" if breach else "success",
                "is_breach": breach,
                "observed_at_str": observed[:80] or _UNKNOWN_STAMP,
                "observed_at": parse_observed_at(observed),
            }
        )
    return rows


async def record_runs(session: Any, samples: list[Any], workflow: str | None) -> int:
    """Persist the distinct readings a sweep observed. Conflict-nothing on
    the dedup key, so re-observing the same run is a no-op. Commits the
    session - call immediately after the fetch, before any writes. Never
    raises into the sweep. Returns the rows inserted."""
    rows = _rows_from_samples(samples, workflow)
    if not rows:
        return 0
    try:
        result = await session.execute(
            insert(CiRunTable).on_conflict_do_nothing(index_elements=_DEDUP_ELEMENTS),
            rows,
        )
        await session.commit()
        return int(result.rowcount or 0)
    except Exception as exc:
        logger.debug("ci history write skipped", error=str(exc))
        return 0


async def runs_for(
    session: Any, project_slug: str, since: datetime, until: datetime
) -> list[CiRunTable]:
    """The post-decision run sequence for one project, oldest first."""
    result = await session.execute(
        select(CiRunTable)
        .where(
            CiRunTable.project_slug == project_slug[:200],
            CiRunTable.created_at >= since,
            CiRunTable.created_at <= until,
        )
        .order_by(CiRunTable.created_at.asc(), CiRunTable.observed_at.asc())
        .limit(200)
    )
    return list(result.scalars())


async def prune(session: Any, days: int = _RETENTION_DAYS) -> int:
    """Delete run history past its retention window. Best-effort."""
    try:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        result = await session.execute(
            delete(CiRunTable).where(CiRunTable.created_at < cutoff)
        )
        return int(result.rowcount or 0)
    except Exception as exc:
        logger.debug("ci history prune skipped", error=str(exc))
        return 0
