"""Custom regex rules from the project's standard, scoped by language.

A custom rule with an empty ``languages`` list applies to every language. A
malformed pattern abstains (it is skipped, not fatal) so a typo in the file
can never strand a task on the block gate.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .findings import Finding

if TYPE_CHECKING:
    from roboco.foundation.policy.conventions.models import (
        ConventionsStandard,
        CustomRule,
    )

# Dialect relations: the validator tags a ``.tsx`` file as language ``tsx``
# (the JSX grammar needs that tag, distinct from plain ``typescript``), but a
# custom rule scoped to ``typescript`` — the language the scan *reports* for a
# React+TS repo — must still apply to ``.tsx`` files. The relation is
# one-directional: ``tsx`` is a TypeScript dialect, so a ``typescript``-scoped
# rule fires on ``.tsx``, but a ``tsx``-scoped (JSX-only) rule does not fire on
# plain ``.ts``. Map each dialect tag to the language family it belongs to.
_DIALECT_OF: dict[str, str] = {"tsx": "typescript"}


def _rule_applies(rule_languages: list[str], file_language: str) -> bool:
    """Whether a custom rule scoped to ``rule_languages`` applies to a file of
    ``file_language``. An unscoped rule (empty list) applies to everything."""
    if not rule_languages:
        return True
    if file_language in rule_languages:
        return True
    family = _DIALECT_OF.get(file_language)
    return family is not None and family in rule_languages


def check_custom(
    rel_path: str, source: bytes, language: str, standard: ConventionsStandard
) -> list[Finding]:
    """Return findings for each custom rule that matches ``source``."""
    text = source.decode(errors="replace")
    findings: list[Finding] = []
    for rule in standard.custom:
        if not _rule_applies(rule.languages, language):
            continue
        findings.extend(_matches(rel_path, text, rule))
    return findings


def _matches(rel_path: str, text: str, rule: CustomRule) -> list[Finding]:
    try:
        pattern = re.compile(rule.pattern)
    except re.error:
        return []
    return [
        Finding(
            file=rel_path,
            line=text.count("\n", 0, match.start()) + 1,
            kind=None,
            rule=rule.id,
            level=rule.level,
            message=rule.message,
            fix_hint=f"matches custom rule '{rule.id}'",
        )
        for match in pattern.finditer(text)
    ]
