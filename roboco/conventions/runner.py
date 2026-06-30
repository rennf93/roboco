"""Run all check families over a set of changed files, then filter waivers.

Dispatch is by file extension; an unsupported extension or a missing file is
skipped. A grammar failure is *fail-loud*: the runner raises
``ValidatorCouldNotRun`` so the gate blocks rather than passing silently.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from . import classify_python, classify_ts
from .custom import check_custom, unrecognized_rule_languages
from .findings import Finding
from .grammars import GrammarUnavailable
from .hygiene import check_hygiene
from .modularity import check_modularity
from .placement import check_placement

if TYPE_CHECKING:
    from roboco.foundation.policy.conventions.models import ConventionsStandard

    from .placement import Definition

_LANGUAGE_BY_SUFFIX = {".py": "python", ".ts": "typescript", ".tsx": "tsx"}

# Surfaced on the conventions file itself — a custom rule scoped to a language
# the validator never reports (a typo) would silently never fire (#129).
_LANGUAGE_SCOPE_RULE = "custom_language_scope"
_LANGUAGE_SCOPE_FILE = ".roboco/conventions.yml"


class ValidatorCouldNotRun(RuntimeError):
    """Raised when the validator cannot analyze a file (fail-loud signal)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def run(
    root: Path | str, files: list[str], standard: ConventionsStandard
) -> list[Finding]:
    """Check ``files`` (repo-relative) under ``root`` against ``standard``."""
    root_path = Path(root)
    findings: list[Finding] = list(_language_scope_findings(standard))
    for rel in files:
        language = _LANGUAGE_BY_SUFFIX.get(Path(rel).suffix)
        if language is not None:
            findings.extend(_check_file(root_path, rel, language, standard))
    return _apply_waivers(findings, standard)


def _language_scope_findings(standard: ConventionsStandard) -> list[Finding]:
    """Warn-once per custom rule scoped to a language the validator never reports."""
    out: list[Finding] = []
    for rule in standard.custom:
        for tag in unrecognized_rule_languages(rule):
            out.append(
                Finding(
                    file=_LANGUAGE_SCOPE_FILE,
                    line=0,
                    kind=None,
                    rule=_LANGUAGE_SCOPE_RULE,
                    level="warn",
                    message=(
                        f"custom rule '{rule.id}' scopes to unknown language"
                        f" '{tag}' — the validator reports {{python, typescript,"
                        " tsx}}; the rule will never fire"
                    ),
                    fix_hint="fix the language tag in .roboco/conventions.yml",
                )
            )
    return out


def _check_file(
    root: Path, rel: str, language: str, standard: ConventionsStandard
) -> list[Finding]:
    try:
        source = (root / rel).read_bytes()
    except OSError:
        return []
    try:
        defs = _classify(language, source)
    except GrammarUnavailable as exc:
        raise ValidatorCouldNotRun(str(exc)) from exc
    return (
        check_placement(rel, defs, standard)
        + check_hygiene(rel, source, language, standard)
        + check_custom(rel, source, language, standard)
        + check_modularity(rel, defs, source, language, standard)
    )


def _classify(language: str, source: bytes) -> list[Definition]:
    if language == "python":
        return classify_python.classify_definitions(source)
    return classify_ts.classify_definitions(source, language)


def _apply_waivers(
    findings: list[Finding], standard: ConventionsStandard
) -> list[Finding]:
    waived = {(w.path, w.rule) for w in standard.waivers}
    return [f for f in findings if (f.file, f.rule) not in waived]
