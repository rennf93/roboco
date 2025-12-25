"""
Factory Base Utilities

Shared utilities for agent factory functions.
"""

import re
from pathlib import Path


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
