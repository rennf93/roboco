"""bash-guard Makefile guardrail — deny raw uv/pip/conda/poetry, point at make.

CEO item: force agents to the Makefile. The existing hook deliberately allowed
bare ``uv run`` (workspace .venv, cwd-relative); this guard overrides that by
CEO direction when a ``Makefile`` is present, denying raw package-manager /
test-runner commands and remediating to the make targets. Skipped when no
Makefile exists so Makefile-less projects aren't blocked. On the grok path
(``ROBOCO_GUARD_SKIP_PM=1``) a deny cancels the whole run, so it nudges (exit 0)
instead.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "docker" / "scripts" / "bash-guard-hook.sh"


def _run_hook(command: str, cwd: Path, env_over: dict[str, str] | None = None) -> tuple[int, str]:
    payload = json.dumps({"tool_input": {"command": command}})
    import os

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
    )
    return proc.returncode, proc.stderr


def test_denies_uv_run_when_makefile_present() -> None:
    rc, err = _run_hook("uv run pytest", REPO_ROOT)
    assert rc == 2
    assert "make" in err.lower()


def test_denies_pip_install() -> None:
    rc, _ = _run_hook("pip install requests", REPO_ROOT)
    assert rc == 2


def test_denies_compound_uv_run() -> None:
    rc, _ = _run_hook("cd svc && uv run ruff check .", REPO_ROOT)
    assert rc == 2


def test_denies_conda_and_poetry() -> None:
    assert _run_hook("conda install numpy", REPO_ROOT)[0] == 2
    assert _run_hook("poetry run pytest", REPO_ROOT)[0] == 2


def test_allows_make_quality() -> None:
    rc, _ = _run_hook("make quality", REPO_ROOT)
    assert rc != 2


def test_allows_pnpm() -> None:
    rc, _ = _run_hook("pnpm lint", REPO_ROOT)
    assert rc != 2


def test_skips_deny_without_makefile(tmp_path: Path) -> None:
    rc, _ = _run_hook("uv run pytest", tmp_path)
    assert rc != 2


def test_grok_path_nudges_not_denies() -> None:
    """ROBOCO_GUARD_SKIP_PM=1 (grok) -> exit 0 nudge, not run-canceling exit 2."""
    rc, err = _run_hook("uv run pytest", REPO_ROOT, {"ROBOCO_GUARD_SKIP_PM": "1"})
    assert rc == 0
    assert "make" in err.lower()