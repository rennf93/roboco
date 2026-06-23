"""Pure schema for the sequenced-batch collision analyzer.

A ``DraftSurface`` is the collision surface the Prompter declares for one
proposed task (which files/dirs it will touch, whether it adds a migration,
whether it edits a widely-shared surface). The analyzer turns a list of them
into a ``SequencePlan``: a dependency DAG (``edges``) and the topological
``waves`` the existing dependency-gate executes, plus non-blocking ``warnings``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class SequencingError(ValueError):
    """A batch's collision graph cannot be serialized into waves (a cycle, or an
    edge references a non-existent draft)."""


@dataclass
class DraftSurface:
    """One proposed task's collision surface (indexed within the batch).

    ``idx`` is the task's position in the batch; ``priority`` is its task
    priority (lower number = more important, runs first on a collision tie).
    ``project_id`` is the repo the task targets — collisions are scoped to it, so
    two tasks in different repos never collide on a coincidentally-equal path or a
    migration (each repo has its own working tree and migration chain).
    """

    idx: int
    priority: int
    intends_to_touch: list[str]
    adds_migration: bool
    touches_shared: bool
    project_id: str | None = None


@dataclass
class SequencePlan:
    """The analyzer's output.

    ``edges`` are ``(a, b)`` pairs meaning *b depends on a* (a runs first);
    ``waves`` is the Kahn topological layering each item's ``dependency_ids``
    are wired from; ``warnings`` are non-blocking advisories (e.g. cell
    contention) that never add an edge.
    """

    edges: list[tuple[int, int]]
    waves: list[list[int]]
    warnings: list[str] = field(default_factory=list)
