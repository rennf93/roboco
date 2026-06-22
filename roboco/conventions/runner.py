"""Run all check families over a set of changed files, then filter waivers.

Dispatch is by file extension; an unsupported extension or a missing file is
skipped. A grammar failure is *fail-loud*: the runner raises
``ValidatorCouldNotRun`` so the gate blocks rather than passing silently.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from . import classify_python, classify_ts
from .custom import check_custom
from .grammars import GrammarUnavailable
from .hygiene import check_hygiene
from .placement import check_placement

if TYPE_CHECKING:
    from roboco.foundation.policy.conventions.models import ConventionsStandard

    from .findings import Finding
    from .placement import Definition

_LANGUAGE_BY_SUFFIX = {".py": "python", ".ts": "typescript", ".tsx": "tsx"}


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
    findings: list[Finding] = []
    for rel in files:
        language = _LANGUAGE_BY_SUFFIX.get(Path(rel).suffix)
        if language is not None:
            findings.extend(_check_file(root_path, rel, language, standard))
    return _apply_waivers(findings, standard)


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
