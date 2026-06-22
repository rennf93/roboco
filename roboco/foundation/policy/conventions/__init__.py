"""Architectural-conventions standard: schema models + effective-map merge.

Pure foundation layer (no IO/DB). The validator CLI (``roboco.conventions``),
``ConventionsService``, and the gateway gates all build on these types.
"""

from __future__ import annotations

from .effective_map import effective_map
from .models import (
    BUILTIN_RULES,
    ConventionsParseError,
    ConventionsStandard,
    CustomRule,
    DefinitionKind,
    Module,
    Rule,
    RuleLevel,
    Waiver,
)

__all__ = [
    "BUILTIN_RULES",
    "ConventionsParseError",
    "ConventionsStandard",
    "CustomRule",
    "DefinitionKind",
    "Module",
    "Rule",
    "RuleLevel",
    "Waiver",
    "effective_map",
]
