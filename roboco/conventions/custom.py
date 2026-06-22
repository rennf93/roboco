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


def check_custom(
    rel_path: str, source: bytes, language: str, standard: ConventionsStandard
) -> list[Finding]:
    """Return findings for each custom rule that matches ``source``."""
    text = source.decode(errors="replace")
    findings: list[Finding] = []
    for rule in standard.custom:
        if rule.languages and language not in rule.languages:
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
