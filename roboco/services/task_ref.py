"""Resolve a task reference to one task id: the single source of truth.

Every surface shows tasks by their 8-char id prefix (``#372eac39`` on the
panel cards, in agent prompts and commit subjects), and people and agents pass
that prefix back. The agent gateway (``ContentActions._resolve_task_ref``) and
the REST task routes both resolve through :func:`resolve_task_ref` and only
translate its outcome into their own error vocabulary (an Envelope for agents,
an HTTP status for the REST API), so the two can never disagree about what a
reference means.

A full UUID passes through without a lookup. A hex prefix of at least
:data:`MIN_TASK_REF_HEX_CHARS` characters (an optional leading ``#`` is
ignored) resolves by an id-prefix lookup that stops at the second match: the
caller only ever needs to tell "none", "exactly one" and "more than one" apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import String, select

from roboco.db.tables import TaskTable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Shortest prefix that identifies a task: the cards render 8 characters.
MIN_TASK_REF_HEX_CHARS = 8

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

# Two rows are enough to prove a prefix ambiguous; reading more is waste.
_AMBIGUITY_PROBE_LIMIT = 2


class TaskRefOutcome(StrEnum):
    """What a task reference turned out to be."""

    RESOLVED = "resolved"
    MALFORMED = "malformed"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TaskRefResolution:
    """The outcome of resolving one reference; ``task_id`` is set only when
    ``outcome`` is :attr:`TaskRefOutcome.RESOLVED`."""

    outcome: TaskRefOutcome
    task_id: UUID | None = None


def task_ref_hex_prefix(ref: str) -> str | None:
    """The hex prefix a reference carries, or None when it cannot name a task
    (too short, or not hexadecimal)."""
    prefix = ref.strip().removeprefix("#").strip()
    if len(prefix) < MIN_TASK_REF_HEX_CHARS:
        return None
    if not set(prefix) <= _HEX_DIGITS:
        return None
    return prefix.lower()


def parse_full_task_uuid(ref: str) -> UUID | None:
    """The reference as a full UUID, or None when it is not one."""
    try:
        return UUID(ref.strip())
    except ValueError:
        return None


async def _task_ids_with_prefix(session: AsyncSession, prefix: str) -> list[UUID]:
    result = await session.execute(
        select(TaskTable.id)
        .where(cast("Any", TaskTable.id).cast(String).ilike(f"{prefix}%"))
        .limit(_AMBIGUITY_PROBE_LIMIT)
    )
    return [row[0] for row in result.all()]


async def resolve_task_ref(session: AsyncSession, ref: str) -> TaskRefResolution:
    """Resolve ``ref`` (a full UUID or an 8+ char hex prefix) to one task id."""
    full_uuid = parse_full_task_uuid(ref)
    if full_uuid is not None:
        return TaskRefResolution(TaskRefOutcome.RESOLVED, full_uuid)
    prefix = task_ref_hex_prefix(ref)
    if prefix is None:
        return TaskRefResolution(TaskRefOutcome.MALFORMED)
    matches = await _task_ids_with_prefix(session, prefix)
    if not matches:
        return TaskRefResolution(TaskRefOutcome.NOT_FOUND)
    if len(matches) > 1:
        return TaskRefResolution(TaskRefOutcome.AMBIGUOUS)
    return TaskRefResolution(TaskRefOutcome.RESOLVED, matches[0])
