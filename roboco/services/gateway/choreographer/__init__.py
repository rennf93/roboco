"""Gateway choreographer package.

The single-file 2,540-line ``choreographer.py`` is being split into
per-role mixins composed onto a single ``Choreographer`` class.
``board.py`` was the first extraction (Board + Auditor verbs); the verbs
not yet split out live on ``BaseChoreographer`` in ``_impl.py`` and move
incrementally.

The public surface (``Choreographer``, ``ChoreographerDeps``,
``DelegateInputs``) is re-exported here so every caller's import path
``from roboco.services.gateway.choreographer import Choreographer``
continues to resolve.
"""

from __future__ import annotations

from roboco.services.gateway.choreographer._impl import (
    BaseChoreographer,
    BranchSyncMixin,
    BriefingMixin,
    ChoreographerDeps,
    ClaimGuardsMixin,
    ClaimPlanMixin,
    DelegateInputs,
    EscalationMixin,
    IdleGuardsMixin,
    IdleMixin,
    LeafClosureMixin,
    OwnershipMixin,
    QualityGuardsMixin,
    RejectionMixin,
    ResolvedFindingsMixin,
    TracingGatesMixin,
)
from roboco.services.gateway.choreographer.board import BoardMixin
from roboco.services.gateway.choreographer.dev import (
    DevClaimMixin,
    DoneChecksMixin,
    GiveMeWorkMixin,
    IAmDoneMixin,
    OpenPRMixin,
)
from roboco.services.gateway.choreographer.doc import DocMixin
from roboco.services.gateway.choreographer.pm import (
    CompleteMixin,
    DelegateMixin,
    PMReviewMixin,
    PMWorkMixin,
    SiblingDedupMixin,
    SubmitRootMixin,
    SubmitUpMixin,
    SubtaskCreateMixin,
)
from roboco.services.gateway.choreographer.pr_gate import PRGateMixin
from roboco.services.gateway.choreographer.pr_review import PRReviewerMixin
from roboco.services.gateway.choreographer.qa import QAMixin


class Choreographer(
    BoardMixin,
    DocMixin,
    QAMixin,
    PRReviewerMixin,
    PRGateMixin,
    DevClaimMixin,
    DoneChecksMixin,
    GiveMeWorkMixin,
    IAmDoneMixin,
    OpenPRMixin,
    CompleteMixin,
    DelegateMixin,
    PMReviewMixin,
    PMWorkMixin,
    SiblingDedupMixin,
    SubmitRootMixin,
    SubmitUpMixin,
    SubtaskCreateMixin,
    BranchSyncMixin,
    BriefingMixin,
    ClaimGuardsMixin,
    ClaimPlanMixin,
    EscalationMixin,
    IdleGuardsMixin,
    IdleMixin,
    LeafClosureMixin,
    OwnershipMixin,
    QualityGuardsMixin,
    RejectionMixin,
    ResolvedFindingsMixin,
    TracingGatesMixin,
    BaseChoreographer,
):
    """Composed choreographer.

    MRO walks left-to-right: extracted mixins resolve first, then
    ``BaseChoreographer`` supplies everything not yet split out (helpers,
    deps, __init__, the remaining role verbs). Future per-role splits add
    mixins to the left of ``BaseChoreographer`` in this declaration.
    """


__all__ = ["Choreographer", "ChoreographerDeps", "DelegateInputs"]
