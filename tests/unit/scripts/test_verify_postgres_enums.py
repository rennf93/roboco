"""Pure decision logic of scripts/verify_postgres_enums.py.

The verifier is a gate script: it must NOT report drift against an empty /
unmigrated DB (no enum types → "no migrated target", not drift), and it must
distinguish unreachable (skip) from real drift (fail) so the Makefile mask
can't swallow drift into a false-green. These tests pin the two pure
predicates behind the I/O shell; the asyncpg connect/fetch path is exercised
by the live empty-`roboco`-DB smoke (`test_smoke_empty_db_skips`).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "scripts" / "verify_postgres_enums.py"

# Load the script as an isolated module (it's a gate script, not a package).
_spec = importlib.util.spec_from_file_location("verify_postgres_enums", SCRIPT)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
enum_drift = _mod.enum_drift
should_skip_for_unmigrated = _mod.should_skip_for_unmigrated

_SKIP = 0


# ---------------------------------------------------------------------------
# enum_drift — pure set comparison
# ---------------------------------------------------------------------------


def test_enum_drift_exact_match_is_no_drift() -> None:
    roles = {"developer", "qa"}
    teams = {"backend", "frontend"}
    has_drift, msgs = enum_drift(roles, teams, roles.copy(), teams.copy())
    assert has_drift is False
    assert msgs == []


def test_enum_drift_missing_role() -> None:
    has_drift, msgs = enum_drift(
        {"developer"}, {"backend"}, {"developer", "qa"}, {"backend"}
    )
    assert has_drift is True
    assert any("agentrole missing" in m and "qa" in m for m in msgs)


def test_enum_drift_extra_role() -> None:
    has_drift, msgs = enum_drift(
        {"developer", "ghost"}, {"backend"}, {"developer"}, {"backend"}
    )
    assert has_drift is True
    assert any("agentrole has extra" in m and "ghost" in m for m in msgs)


def test_enum_drift_missing_team() -> None:
    has_drift, msgs = enum_drift(
        {"developer"}, {"backend"}, {"developer"}, {"backend", "ux_ui"}
    )
    assert has_drift is True
    assert any("team missing" in m and "ux_ui" in m for m in msgs)


def test_enum_drift_extra_team() -> None:
    has_drift, msgs = enum_drift(
        {"developer"}, {"backend", "phantom"}, {"developer"}, {"backend"}
    )
    assert has_drift is True
    assert any("team has extra" in m and "phantom" in m for m in msgs)


# ---------------------------------------------------------------------------
# should_skip_for_unmigrated — only skip when BOTH enum types are absent
# ---------------------------------------------------------------------------


def test_skip_when_both_enum_types_absent() -> None:
    assert should_skip_for_unmigrated(False, False) is True


def test_no_skip_when_only_agentrole_present() -> None:
    # A partial schema is itself suspicious — do not skip; let drift fire.
    assert should_skip_for_unmigrated(True, False) is False


def test_no_skip_when_only_team_present() -> None:
    assert should_skip_for_unmigrated(False, True) is False


def test_no_skip_when_both_present() -> None:
    assert should_skip_for_unmigrated(True, True) is False


# ---------------------------------------------------------------------------
# Live smoke — the empty `roboco` DB on this host must skip (exit 0), not
# false-positive drift (exit 1). Skipped silently if no DB is reachable.
# ---------------------------------------------------------------------------


def test_smoke_empty_db_skips() -> None:
    """Against an empty/unmigrated DB the verifier skips (exit 0)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if (
        result.returncode == _SKIP
        and "skipped" in (result.stdout + result.stderr).lower()
    ):
        # A reachable-but-empty DB → skip path. (No DB → also exit 0 skip.)
        assert "DRIFT" not in result.stdout
        return
    # If postgres is unreachable on this host, the smoke is a no-op skip.
    assert result.returncode == _SKIP, (
        f"expected skip exit 0, got {result.returncode}; out={result.stdout!r}"
    )
