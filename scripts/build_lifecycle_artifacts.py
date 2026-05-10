#!/usr/bin/env python3
"""Regenerate lifecycle artifacts from roboco/foundation/policy/lifecycle.py.

Outputs (deterministic):
  - docs/rag/lifecycle/intent-verbs.md
  - docs/rag/lifecycle/status-transitions.md
  - panel/lib/lifecycle.json
  - agents/prompts/_generated/lifecycle-{role}.md  (one per role)

Run as part of `make lifecycle`. CI gate: `make lifecycle && git diff
--exit-code` fails if regeneration produces a diff.
"""

from __future__ import annotations

from pathlib import Path

from roboco.foundation import _generators
from roboco.foundation.policy.lifecycle import Role

REPO_ROOT = Path(__file__).resolve().parent.parent


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f"wrote {path.relative_to(REPO_ROOT)}")


def main() -> int:
    write(
        REPO_ROOT / "docs" / "rag" / "lifecycle" / "intent-verbs.md",
        _generators.render_intent_verbs_md(),
    )
    write(
        REPO_ROOT / "docs" / "rag" / "lifecycle" / "status-transitions.md",
        _generators.render_status_transitions_md(),
    )
    write(
        REPO_ROOT / "panel" / "lib" / "lifecycle.json",
        _generators.render_panel_json(),
    )
    for role in Role:
        write(
            REPO_ROOT
            / "agents"
            / "prompts"
            / "_generated"
            / f"lifecycle-{role.value}.md",
            _generators.render_agent_prompt_fragment(role.value),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
