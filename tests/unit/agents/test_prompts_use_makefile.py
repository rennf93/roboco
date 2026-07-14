"""Agent prompts must steer the fleet at the Makefile, not raw package managers.

`agents/prompts/teams/backend.md` used to literally instruct `uv run ruff ...`
(copied from the human-facing CLAUDE.md), so agents ran raw `uv run` and bypassed
the Makefile's `UV_NO_SYNC=1` + private `UV_CACHE_DIR` — the guard against the
concurrent-venv-corruption race. This grep invariant keeps the prompts honest:
no agent-facing prompt instructs a raw `uv run`/`pip`/`conda`/`poetry`, and the
backend standards block names the make targets.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[3] / "agents" / "prompts"
RAW_PM = ["uv run ", "uv pip install", "pip install ", "conda install ", "poetry run "]


def _prompt_texts() -> dict[str, str]:
    texts: dict[str, str] = {}
    for path in PROMPTS.rglob("*.md"):
        texts[str(path.relative_to(PROMPTS))] = path.read_text()
    return texts


def test_no_prompt_instructs_raw_package_managers() -> None:
    """No agent-facing prompt tells an agent to run raw uv/pip/conda/poetry."""
    offenders: list[str] = []
    for name, body in _prompt_texts().items():
        for bad in RAW_PM:
            if bad in body:
                offenders.append(f"{name}: '{bad.strip()}'")
    assert not offenders, f"prompts still instruct raw PM commands: {offenders}"


def test_backend_prompt_names_make_targets() -> None:
    """The backend standards block must point at make targets, not uv run."""
    body = (PROMPTS / "teams" / "backend.md").read_text()
    assert "make quality" in body
    assert "make gate" in body