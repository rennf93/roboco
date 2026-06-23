"""Sequenced batch intake — pure collision-sequencing schema.

The deterministic ``SequencingService`` (``roboco/services/sequencing.py``) reads
these dataclasses; nothing here touches the DB or any service. ``DraftSurface``
is one proposed task's collision surface; ``SequencePlan`` is the dependency DAG
(edges) + the topological waves + any cell-contention warnings.
"""

from __future__ import annotations

from roboco.foundation.policy.sequencing.models import (
    DraftSurface,
    SequencePlan,
    SequencingError,
)

__all__ = ["DraftSurface", "SequencePlan", "SequencingError"]
