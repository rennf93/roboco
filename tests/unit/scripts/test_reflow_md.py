"""Guard the hard-wrap reflow gate: wiring into `make quality` + the check exit code.

Nothing previously pinned that `make quality` actually runs
`scripts/reflow_md.py --check` — a `Makefile` edit could silently drop the
line and no test would notice the regression. These tests pin the wiring and
the script's own pass/fail exit codes behind the I/O shell.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / "scripts" / "reflow_md.py"
MAKEFILE = ROOT / "Makefile"


def test_quality_target_wires_in_reflow_check() -> None:
    text = MAKEFILE.read_text()
    quality_block = text.split("\n.PHONY: quality\n", 1)[1].split("\n.PHONY: ", 1)[0]
    assert "scripts/reflow_md.py --check" in quality_block, (
        "make quality must run scripts/reflow_md.py --check "
        "(the hard-wrap reflow gate) — wiring was removed"
    )


def test_check_passes_on_repo_as_committed() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK: no hard-wrapped markdown prose in scope." in result.stdout


def test_check_fails_on_a_hard_wrapped_file(tmp_path: Path) -> None:
    wrapped = tmp_path / "sample.md"
    wrapped.write_text(
        "This is a paragraph that has been\n"
        "hard-wrapped mid-sentence across two\n"
        "separate lines for no good reason.\n"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "hard-wrapped prose" in result.stdout
