"""Base-red submission gate.

While a base-red incident is open for a task's project, ``open_pr`` and
``i_am_done`` refuse with a remediate envelope naming the incident owner, so
devs stop resubmitting unchanged heads into a red base. The incident owner's
own fix task is exempt, and a failing lookup fails open — the gate may only
ever suppress a submission it is sure about.

The incident service (``roboco.services.base_red_incident``) is consumed
through the ``BaseRedIncidentSource`` Protocol so tests can inject a fake.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from uuid import UUID

log = structlog.get_logger(__name__)

AUDIT_EVENT_CLEARED = "base_red.cleared"


class BaseRedIncidentSource(Protocol):
    """The slice of the incident service the gate consumes."""

    async def get_open_incident(self, project_id: UUID) -> Any | None: ...

    async def is_owner_task(self, task_id: UUID) -> bool: ...

    async def clear(self, project_id: UUID, cleared_by: str, reason: str) -> Any: ...


def resolve_incident_service(session: Any) -> BaseRedIncidentSource | None:
    """Build the real incident service, or ``None`` while its module is absent.

    The accessor name is looked up lazily so this gate stays inert (never an
    ImportError at startup) until the detection service lands.
    """
    try:
        module = importlib.import_module("roboco.services.base_red_incident")
        factory = getattr(module, "get_base_red_incident_service", None)
        return factory(session) if factory is not None else None
    except ImportError:
        return None


def _refusal(incident: Any) -> Envelope:
    owner = f"{incident.owner_agent} (task {incident.owner_task_id})"
    return Envelope.not_authorized(
        message=(
            f"base branch '{incident.base_branch}' is red (base-red incident "
            f"owned by {owner}); submitting now would only re-run CI against "
            "a failing base"
        ),
        remediate=(
            f"wait for {owner} to fix '{incident.base_branch}', then retry; "
            "if you believe the base is green, ask your PM for a manual clear "
            "of the base-red incident"
        ),
    )


async def base_red_submission_guard(
    source: BaseRedIncidentSource | None, task: Any
) -> Envelope | None:
    """Return a refusal envelope while the task's project has an open incident."""
    project_id = getattr(task, "project_id", None)
    if source is None or project_id is None:
        return None
    try:
        incident = await source.get_open_incident(project_id)
        if incident is None or await source.is_owner_task(task.id):
            return None
    except Exception:
        log.warning("base_red_gate_lookup_failed", task_id=str(task.id), exc_info=True)
        return None
    return _refusal(incident)


async def clear_base_red_incident(
    source: BaseRedIncidentSource,
    audit: Any,
    *,
    project_id: UUID,
    cleared_by: str,
    reason: str,
) -> None:
    """Manually clear the project's incident and write the audit row."""
    await source.clear(project_id, cleared_by, reason)
    await audit.log_event(
        event_type=AUDIT_EVENT_CLEARED,
        agent_id=cleared_by,
        details={"project_id": str(project_id), "reason": reason},
        severity="info",
    )
