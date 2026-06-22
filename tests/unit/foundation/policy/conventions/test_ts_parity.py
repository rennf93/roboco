"""The panel TS ConventionsStandard type mirrors the Python model fields.

A drift here means the panel editor and the backend disagree on the shape of
``.roboco/conventions.yml`` — caught at test time, not in production.
"""

from __future__ import annotations

import re
from pathlib import Path

from roboco.foundation.policy.conventions.models import ConventionsStandard

_REPO_ROOT = Path(__file__).resolve().parents[5]
_TS_FILE = _REPO_ROOT / "panel" / "src" / "lib" / "api" / "conventions.ts"


def _ts_interface_fields(text: str, name: str) -> set[str]:
    match = re.search(rf"export interface {name} \{{(.+?)\n\}}", text, re.DOTALL)
    assert match, f"interface {name} not found in conventions.ts"
    return set(re.findall(r"^\s*(\w+)\s*[?:]", match.group(1), re.MULTILINE))


def test_ts_standard_matches_python_fields() -> None:
    text = _TS_FILE.read_text()
    ts_keys = _ts_interface_fields(text, "ConventionsStandard")
    py_keys = set(ConventionsStandard.model_fields.keys())
    assert ts_keys == py_keys
