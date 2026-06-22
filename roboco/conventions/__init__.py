"""The ``roboco-conventions`` validator: tree-sitter placement + hygiene checks.

A single Python CLI (``python -m roboco.conventions``) classifies each changed
definition and flags forbidden placements, hygiene violations, and custom-rule
matches against a project's effective conventions map. Precision over recall,
fail loud: an ambiguous definition abstains; a validator that cannot run exits
non-zero so the gate blocks rather than silently passing.
"""

from __future__ import annotations

from .classify_python import classify_definitions
from .custom import check_custom
from .findings import Finding
from .grammars import GrammarUnavailable, get_parser
from .hygiene import check_hygiene
from .placement import check_placement
from .runner import ValidatorCouldNotRun, run

__all__ = [
    "Finding",
    "GrammarUnavailable",
    "ValidatorCouldNotRun",
    "check_custom",
    "check_hygiene",
    "check_placement",
    "classify_definitions",
    "get_parser",
    "run",
]
