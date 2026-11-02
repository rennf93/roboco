"""Gateway choreographer package.

The single-file ``choreographer.py`` is split into mixins composed onto a
single ``Choreographer`` class, none over the conventions check's 15-method
limit: the per-role verbs (``board.py``, ``doc.py``, ``qa.py``,
``pr_review.py``, ``pr_gate*.py``, ``dev.py``, ``pm.py``), and in ``_impl.py``
``BaseChoreographer`` with the mixins every role shares.

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

    MRO walks left-to-right: the mixins resolve first, then
    ``BaseChoreographer`` supplies the deps, ``__init__`` and the service
    properties. No two classes define the same method.
    """


__all__ = ["Choreographer", "ChoreographerDeps", "DelegateInputs"]
