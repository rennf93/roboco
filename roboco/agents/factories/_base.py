"""
Factory Base Utilities

Shared utilities for agent factory functions.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roboco.models import AgentRole, Team


def _get_prompts_base_path() -> Path:
    """Get the base path for prompt layers.

    Resolves to project_root/agents/prompts/ by finding the roboco package
    and going up one level.
    """
    # Start from this file's location: roboco/agents/factories/_base.py
    # Go up: factories -> agents -> roboco -> project_root
    this_file = Path(__file__).resolve()
    project_root = this_file.parent.parent.parent.parent
    prompts_path = project_root / "agents" / "prompts"

    # Fallback: try relative path if above doesn't exist
    if not prompts_path.exists():
        prompts_path = Path("agents/prompts")

    return prompts_path


# Base path for prompt layers
PROMPTS_BASE_PATH = _get_prompts_base_path()


def _load_layer(layer_path: Path) -> str:
    """
    Load a prompt layer file.

    Args:
        layer_path: Path to the layer markdown file

    Returns:
        File content or empty string if not found
    """
    if not layer_path.exists():
        return ""
    return layer_path.read_text().strip()


_ROLE_LAYER_MAP: dict[str, str] = {
    # Cell members
    "developer": "developer.md",
    "qa": "qa.md",
    "documenter": "documenter.md",
    # PMs have separate files
    "main_pm": "main_pm.md",
    "cell_pm": "cell_pm.md",
    # Board members use board layer
    "product_owner": "board.md",
    "head_marketing": "board.md",
    "auditor": "board.md",
}

_TEAM_LAYER_MAP: dict[str, str] = {
    "backend": "backend.md",
    "frontend": "frontend.md",
    "ux_ui": "ux_ui.md",
}


def _role_layer(prompts_path: Path, role: "AgentRole") -> str | None:
    """Load the role-specific prompt layer, or None if unknown."""
    role_value = role.value if hasattr(role, "value") else str(role)
    role_file = _ROLE_LAYER_MAP.get(role_value)
    if not role_file:
        return None
    return _load_layer(prompts_path / "roles" / role_file)


def _team_layer(prompts_path: Path, team: "Team | None") -> str | None:
    """Load the team-specific prompt layer, or None if unset/unknown."""
    if not team:
        return None
    team_value = team.value if hasattr(team, "value") else str(team)
    team_file = _TEAM_LAYER_MAP.get(team_value)
    if not team_file:
        return None
    return _load_layer(prompts_path / "teams" / team_file)


def compose_prompt(
    role: "AgentRole",
    team: "Team | None",
    agent_slug: str,
    base_path: Path | None = None,
) -> str:
    """
    Compose a system prompt from layered components.

    Combines:
    1. base.md - Universal rules (all agents)
    2. roles/{role}.md - Role-specific behavior
    3. teams/{team}.md - Team context (if team is set)
    4. identities/{agent_slug}.md - Agent identity

    Args:
        role: Agent's role (developer, qa, pm, documenter, board)
        team: Agent's team (backend, frontend, ux_ui, or None for board)
        agent_slug: Agent's slug identifier (e.g., "be-dev-1")
        base_path: Optional override for prompts base path

    Returns:
        Composed system prompt string
    """
    prompts_path = base_path or PROMPTS_BASE_PATH
    parts: list[str] = []

    for layer in (
        _load_layer(prompts_path / "base.md"),
        _role_layer(prompts_path, role),
        _team_layer(prompts_path, team),
        _load_layer(prompts_path / "identities" / f"{agent_slug}.md"),
    ):
        if layer:
            parts.append(layer)

    # Join with separator
    return "\n\n---\n\n".join(parts)


def load_blueprint_prompt(blueprint_path: str, default_prompt: str) -> str:
    """
    Load system prompt from a blueprint file.

    Args:
        blueprint_path: Relative path to the blueprint markdown file
        default_prompt: Default prompt if file doesn't exist or parsing fails

    Returns:
        The extracted system prompt or the default
    """
    path = Path(blueprint_path)
    if not path.exists():
        return default_prompt

    content = path.read_text()
    # Extract system prompt section (between ```blocks after ## System Prompt)
    match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
    return match.group(1).strip() if match else default_prompt


def make_slug(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    return name.lower().replace(" ", "-")
