"""bash-guard Makefile guardrail — deny raw uv/pip/conda/poetry, point at make.

CEO item: force agents to the Makefile. The existing hook deliberately allowed
bare ``uv run`` (workspace .venv, cwd-relative); this guard overrides that by
CEO direction when a ``Makefile`` is present AND declares at least one of the
quality/gate/lint/test targets, denying raw package-manager / test-runner
commands and remediating to the make targets. Skipped when no Makefile exists,
or when one exists but declares none of those targets (a Go/Rust Makefile with
only build/run — existence alone would remediate into a dead end). On the grok
path (``ROBOCO_GUARD_SKIP_PM=1``) a deny cancels the whole run, so it nudges
(exit 0) instead.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "docker" / "scripts" / "bash-guard-hook.sh"

# Hook exits 2 to deny, 0 to allow. Named (not magic) for ruff PLR2004.
_DENIED = 2
_ALLOWED = 0


def _run_hook(
    command: str, cwd: Path, env_over: dict[str, str] | None = None
) -> tuple[int, str]:
    payload = json.dumps({"tool_input": {"command": command}})
    env = dict(os.environ)
    if env_over:
        env.update(env_over)
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        check=False,
    )
    return proc.returncode, proc.stderr


def test_denies_uv_run_when_makefile_present() -> None:
    rc, err = _run_hook("uv run pytest", REPO_ROOT)
    assert rc == _DENIED
    assert "make" in err.lower()


def test_denies_pip_install() -> None:
    rc, _ = _run_hook("pip install requests", REPO_ROOT)
    assert rc == _DENIED


def test_denies_compound_uv_run() -> None:
    rc, _ = _run_hook("cd svc && uv run ruff check .", REPO_ROOT)
    assert rc == _DENIED


def test_denies_conda_and_poetry() -> None:
    assert _run_hook("conda install numpy", REPO_ROOT)[0] == _DENIED
    assert _run_hook("poetry run pytest", REPO_ROOT)[0] == _DENIED


def test_allows_make_quality() -> None:
    rc, _ = _run_hook("make quality", REPO_ROOT)
    assert rc != _DENIED


def test_allows_pnpm() -> None:
    rc, _ = _run_hook("pnpm lint", REPO_ROOT)
    assert rc != _DENIED


def test_skips_deny_without_makefile(tmp_path: Path) -> None:
    rc, _ = _run_hook("uv run pytest", tmp_path)
    assert rc != _DENIED


def test_skips_deny_when_makefile_lacks_remediation_targets(tmp_path: Path) -> None:
    """A Go/Rust-style Makefile with only build/run targets — existence alone
    must not deny+remediate to a `make quality`/`gate`/`lint`/`test` that
    doesn't exist (the false-remediation dead-end loop the content check
    closes)."""
    (tmp_path / "Makefile").write_text("build:\n\tgo build ./...\nrun:\n\tgo run .\n")
    rc, _ = _run_hook("uv run pytest", tmp_path)
    assert rc != _DENIED


def test_denies_when_makefile_has_only_one_remediation_target(tmp_path: Path) -> None:
    """Just one of quality/gate/lint/test is enough to arm the deny — the
    guard doesn't require all four."""
    (tmp_path / "Makefile").write_text("lint:\n\truff check .\n")
    rc, _ = _run_hook("uv run pytest", tmp_path)
    assert rc == _DENIED


def test_grok_path_nudges_not_denies() -> None:
    """ROBOCO_GUARD_SKIP_PM=1 (grok) -> exit 0 nudge, not run-canceling exit 2."""
    rc, err = _run_hook("uv run pytest", REPO_ROOT, {"ROBOCO_GUARD_SKIP_PM": "1"})
    assert rc == _ALLOWED
    assert "make" in err.lower()
