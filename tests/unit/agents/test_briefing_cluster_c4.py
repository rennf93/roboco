"""Cluster C4 — agent briefing/onboarding fixes (findings #3, #5, #9).

These assert on the COMPOSED system prompt (``compose_prompt``), which is
the text actually mounted into agent containers at ``/app/system-prompt.md``
via ``--system-prompt-file``. The three behaviors fixed here:

- #3: Main PM must read the upstream PO/HoM handoff BEFORE its own research,
  so it does not duplicate the Board's analysis.
- #5: Main PM's Products-vs-Projects mental model — a Product fans out to
  per-cell Projects which may be the SAME repo (monorepo subtrees) or
  DIFFERENT repos (multi-repo); the Main PM coordinates across them and
  must not assume one repo.
- #9: Developers know their exact workspace path convention, stay inside
  their own cell workspace, and obtain secrets via the task description —
  never ``env``/``printenv`` (bash-guard denies it).
"""

from __future__ import annotations

from roboco.agents.factories._base import compose_prompt
from roboco.models import AgentRole, Team


def _composed_prompt_for(role: AgentRole, team: Team | None = None) -> str:
    return compose_prompt(role, team, agent_slug="test-agent")


# --------------------------------------------------------------------------
# #3 — Main PM consumes the upstream handoff before researching/planning
# --------------------------------------------------------------------------


def test_main_pm_prompt_requires_reading_upstream_handoff_first() -> None:
    """Main PM is told to read the PO/HoM handoff BEFORE its own research."""
    prompt = _composed_prompt_for(AgentRole.MAIN_PM)
    assert "Read the upstream handoff BEFORE you research or plan" in prompt
    # Names both upstream sources explicitly.
    assert "Product Owner" in prompt
    assert "Head of Marketing" in prompt


def test_main_pm_prompt_forbids_re_researching_already_handed_work() -> None:
    """The duplicated-research failure mode is called out as an anti-pattern."""
    prompt = _composed_prompt_for(AgentRole.MAIN_PM)
    assert "Re-researching the codebase from scratch" in prompt
    # The handoff lives in the task journal as decision/reflect entries.
    assert "decision" in prompt and "reflect" in prompt


# --------------------------------------------------------------------------
# #5 — Products-vs-Projects mental model (mono- vs multi-repo)
# --------------------------------------------------------------------------


def test_main_pm_prompt_explains_product_fans_out_to_per_cell_projects() -> None:
    """Main PM prompt distinguishes Product (strategic) from per-cell Projects."""
    prompt = _composed_prompt_for(AgentRole.MAIN_PM)
    assert "Products vs Projects" in prompt
    assert "fans out to one Project per cell" in prompt


def test_main_pm_prompt_covers_both_monorepo_and_multirepo() -> None:
    """Both fan-out shapes are described; neither is assumed by default."""
    prompt = _composed_prompt_for(AgentRole.MAIN_PM)
    assert "Monorepo" in prompt
    assert "Multi-repo" in prompt
    # Must not let the agent call a monorepo subtree "a separate repo".
    assert "not" in prompt and "a separate repo" in prompt


def test_main_pm_prompt_names_prompter_monorepo_case() -> None:
    """The concrete Prompter case (all cells = one repo) is stated."""
    prompt = _composed_prompt_for(AgentRole.MAIN_PM)
    assert "github.com/rennf93/roboco" in prompt


# --------------------------------------------------------------------------
# #9 — Developer workspace path convention + secret handling
# --------------------------------------------------------------------------


def test_developer_prompt_states_exact_workspace_path_convention() -> None:
    """Developer prompt gives the exact /data/workspaces/<slug>/<team>/<slug> path."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    assert "/data/workspaces/<project-slug>/<team>/<agent-slug>/" in prompt
    # Steered to stay inside its own cell workspace.
    assert "Stay inside your own cell workspace" in prompt


def test_developer_prompt_forbids_probing_filesystem_for_workspace() -> None:
    """Developer is told not to probe / guess the workspace path."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    assert "Do NOT probe for it." in prompt
    assert "ls /" in prompt and "find /" in prompt


def test_developer_prompt_gives_sanctioned_secret_path_not_env() -> None:
    """Secrets come via the task description; env/printenv is denied."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    # env/printenv called out as denied + bash-guard blocked.
    assert "printenv" in prompt
    assert "bash-guard" in prompt
    # Sanctioned path: value provided in the task description.
    assert "in the task description" in prompt
    # Escalation route when the value is genuinely missing.
    assert "i_am_blocked" in prompt
