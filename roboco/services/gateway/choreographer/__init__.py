"""Gateway choreographer package.

The single-file 2,540-line ``choreographer.py`` is being split into
per-role mixins composed onto a single ``Choreographer`` class.
``board.py`` is the first extraction (Board + Auditor verbs); the rest
still live in ``_impl.py`` and will move incrementally.

The public surface (``Choreographer``, ``ChoreographerDeps``,
``DelegateInputs``) is re-exported here so every caller's import path
``from roboco.services.gateway.choreographer import Choreographer``
continues to resolve.
"""

from __future__ import annotations

from roboco.services.gateway.choreographer._impl import (
    Choreographer as _LegacyChoreographer,
)
from roboco.services.gateway.choreographer._impl import (
    ChoreographerDeps,
    DelegateInputs,
)
from roboco.services.gateway.choreographer.board import BoardMixin
from roboco.services.gateway.choreographer.doc import DocMixin
from roboco.services.gateway.choreographer.qa import QAMixin


class Choreographer(BoardMixin, DocMixin, QAMixin, _LegacyChoreographer):
    """Composed choreographer.

    MRO walks left-to-right: extracted mixins resolve first, then the
    legacy class supplies everything not yet split out (helpers, deps,
    __init__, all remaining role verbs). Future per-role splits add
    mixins to the left of _LegacyChoreographer in this declaration;
    once everything has moved out, _LegacyChoreographer becomes
    ``BaseChoreographer`` (deps + helpers only).
    """


__all__ = ["Choreographer", "ChoreographerDeps", "DelegateInputs"]
