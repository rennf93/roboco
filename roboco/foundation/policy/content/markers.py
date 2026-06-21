"""Typed accessors for ``Task.orchestration_markers``.

The machine markers that used to be string-packed into the human
``quick_context`` blob (``original_developer:<uuid>``, ``documenter:<uuid>``,
``required_cells:``, ``external_pr_head=``, ``self_heal_fp=``, ``dismissed=1``,
``external_pr_supersede ...``) live in the ``orchestration_markers`` JSON column
after migration 041. These accessors are the single read/write surface for them.

Writes REASSIGN the dict (``task.orchestration_markers = {...}``) rather than
mutate in place, so SQLAlchemy's change tracking flags the column dirty (a plain
JSON column does not detect in-place mutation).
"""

from __future__ import annotations

from typing import Any, Protocol


class HasMarkers(Protocol):
    """Anything carrying the markers column (the ORM task row or domain model)."""

    orchestration_markers: dict[str, Any] | None


# Marker keys — the single source of the vocabulary.
ORIGINAL_DEVELOPER = "original_developer"
DOCUMENTER = "documenter"
REQUIRED_CELLS = "required_cells"
EXTERNAL_PR_HEAD = "external_pr_head"
EXTERNAL_PR_SUPERSEDE = "external_pr_supersede"
SELF_HEAL_FP = "self_heal_fp"
DISMISSED = "dismissed"
ESCALATION = "escalation"
APPROVE_AND_START_NOTES = "approve_and_start_notes"


def get_marker(task: HasMarkers, key: str, default: Any = None) -> Any:
    om = getattr(task, "orchestration_markers", None)
    if not isinstance(om, dict):
        return default
    return om.get(key, default)


def set_marker(task: HasMarkers, key: str, value: Any) -> None:
    om = getattr(task, "orchestration_markers", None)
    markers = dict(om) if isinstance(om, dict) else {}
    markers[key] = value
    task.orchestration_markers = markers


def clear_marker(task: HasMarkers, key: str) -> None:
    om = getattr(task, "orchestration_markers", None)
    if not isinstance(om, dict) or key not in om:
        return
    markers = dict(om)
    del markers[key]
    task.orchestration_markers = markers or None


# --- original developer ---------------------------------------------------- #


def get_original_developer(task: HasMarkers) -> str | None:
    val = get_marker(task, ORIGINAL_DEVELOPER)
    return str(val) if val else None


def set_original_developer(task: HasMarkers, agent_id: Any) -> None:
    set_marker(task, ORIGINAL_DEVELOPER, str(agent_id))


# --- documenter ------------------------------------------------------------ #


def get_documenter(task: HasMarkers) -> str | None:
    val = get_marker(task, DOCUMENTER)
    return str(val) if val else None


def set_documenter(task: HasMarkers, agent_id: Any) -> None:
    set_marker(task, DOCUMENTER, str(agent_id))


# --- required cells -------------------------------------------------------- #


def get_required_cells(task: HasMarkers) -> list[str]:
    val = get_marker(task, REQUIRED_CELLS, [])
    return [str(c) for c in val] if isinstance(val, list) else []


def set_required_cells(task: HasMarkers, cells: list[str]) -> None:
    set_marker(task, REQUIRED_CELLS, [str(c) for c in cells])


# --- self-heal fingerprint ------------------------------------------------- #


def get_self_heal_fingerprint(task: HasMarkers) -> str | None:
    val = get_marker(task, SELF_HEAL_FP)
    return str(val) if val else None


def set_self_heal_fingerprint(task: HasMarkers, fingerprint: str) -> None:
    set_marker(task, SELF_HEAL_FP, fingerprint)


# --- external PR head ------------------------------------------------------ #


def get_external_pr_head(task: HasMarkers) -> str | None:
    val = get_marker(task, EXTERNAL_PR_HEAD)
    return str(val) if val else None


def set_external_pr_head(task: HasMarkers, head_sha: str) -> None:
    set_marker(task, EXTERNAL_PR_HEAD, head_sha)


# --- external PR supersede ------------------------------------------------- #


def get_external_pr_supersede(task: HasMarkers) -> str | None:
    val = get_marker(task, EXTERNAL_PR_SUPERSEDE)
    return str(val) if val else None


def set_external_pr_supersede(task: HasMarkers, marker: str) -> None:
    set_marker(task, EXTERNAL_PR_SUPERSEDE, marker)


# --- dismissed ------------------------------------------------------------- #


def is_dismissed(task: HasMarkers) -> bool:
    return bool(get_marker(task, DISMISSED, False))


def mark_dismissed(task: HasMarkers) -> None:
    set_marker(task, DISMISSED, True)


# --- escalation ------------------------------------------------------------ #
# A coordination event, NOT a developer note. It used to be appended to
# ``dev_notes`` (polluting the developer's space and growing unboundedly on a
# re-escalation loop); it lives here as the latest structured record instead.
# Delivery of the reason to the target is handled by the escalate notification.


def get_escalation(task: HasMarkers) -> dict[str, str] | None:
    val = get_marker(task, ESCALATION)
    return val if isinstance(val, dict) else None


def set_escalation(
    task: HasMarkers, *, from_slug: str, to_slug: str, reason: str
) -> None:
    set_marker(task, ESCALATION, {"from": from_slug, "to": to_slug, "reason": reason})


# --- approve-and-start notes ----------------------------------------------- #
# The CEO's note when approving a board-reviewed coordination root. Used to be
# string-packed into ``quick_context`` as ``approve_and_start_notes:<text>``;
# kept here so ``quick_context`` carries only the human ResumptionNote.


def get_approve_and_start_notes(task: HasMarkers) -> str | None:
    val = get_marker(task, APPROVE_AND_START_NOTES)
    return str(val) if val else None


def set_approve_and_start_notes(task: HasMarkers, notes: str) -> None:
    set_marker(task, APPROVE_AND_START_NOTES, notes)


# --- lifecycle transition notes -------------------------------------------- #
# A PM/CEO note attached to a lifecycle transition (completion, escalate_to_ceo,
# ceo_approval, ceo_rejection). These used to be string-packed into
# ``quick_context`` as ``<event>:<text>`` soup; they live here keyed by event so
# ``quick_context`` carries only the human ResumptionNote.

TRANSITION_NOTES = "transition_notes"


def get_transition_note(task: HasMarkers, event: str) -> str | None:
    notes = get_marker(task, TRANSITION_NOTES)
    if not isinstance(notes, dict):
        return None
    val = notes.get(event)
    return str(val) if val else None


def set_transition_note(task: HasMarkers, event: str, note: str) -> None:
    existing = get_marker(task, TRANSITION_NOTES)
    notes = dict(existing) if isinstance(existing, dict) else {}
    notes[event] = note
    set_marker(task, TRANSITION_NOTES, notes)
