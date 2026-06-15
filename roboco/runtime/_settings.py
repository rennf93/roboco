"""Agent settings generation mixin — role-based permissions + per-agent settings.json.

This module holds the ``AgentSettingsMixin`` class that provides
``_get_role_permissions`` and ``_generate_agent_settings`` to
``AgentOrchestrator``. Extracted from ``orchestrator.py`` to shrink the
monolith; the mixin pattern preserves all existing ``self._get_role_permissions``
and ``self._generate_agent_settings`` call sites unchanged.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import structlog

from roboco.runtime._helpers import DATA_HOST_PATH

logger = structlog.get_logger()


class AgentSettingsMixin:
    """Mixin for AgentOrchestrator: per-agent Claude Code settings generation.

    Provides methods that generate role-specific tool-permission allow/deny
    lists and write the per-agent ``settings.json`` file that Claude Code
    consumes inside each agent container.

    All methods in this mixin assume ``self`` is an ``AgentOrchestrator``
    instance (or anything with the same interface).
    """

    @staticmethod
    def _get_role_permissions(
        role: str, workspace_path: str, cell_workspace_path: str
    ) -> dict[str, list[str]]:
        """Get role-specific allow/deny lists for Claude Code tools.

        Post-gateway shape: every state-changing operation an agent can
        perform routes through ``mcp__roboco-flow__*`` (intent verbs) or
        ``mcp__roboco-do__*`` (content tools -- commit, push, PR, journal,
        notify, message), both granted to every role via ``base_allow``.
        Role-specific configuration here only governs file IO (Write/Edit
        scoping) plus a small handful of legacy native-tool denies that
        remain meaningful for weak models. Read-only git lives in
        ``mcp__roboco-git-readonly__*``.

        Args:
            role: Agent role (developer, qa, documenter, cell_pm, main_pm, etc.)
            workspace_path: Path to agent's own workspace directory
            cell_workspace_path: Path to cell's workspace root (for QA/Docs access)

        Returns:
            Dict with 'allow' and 'deny' lists for Claude Code permissions
        """
        configs: dict[str, dict[str, list[str]]] = {
            "developer": {
                "allow": [
                    f"Write(/{workspace_path}/**)",
                    f"Edit(/{workspace_path}/**)",
                ],
                "deny": [],
            },
            "qa": {
                "allow": [],
                "deny": [
                    "Write(*)",
                    "Edit(*)",
                ],
            },
            "documenter": {
                "allow": [
                    f"Write(/{cell_workspace_path}/**)",
                    f"Edit(/{cell_workspace_path}/**)",
                    "Write(//app/docs/**)",
                    "Edit(//app/docs/**)",
                    "Write(//app/CHANGELOG.md)",
                    "Edit(//app/CHANGELOG.md)",
                    "Write(//app/README.md)",
                    "Edit(//app/README.md)",
                ],
                "deny": [],
            },
            "cell_pm": {
                "allow": [],
                "deny": [
                    "Bash(git commit:*)",
                    "Bash(git push:*)",
                    "Write(*)",
                    "Edit(*)",
                ],
            },
            "main_pm": {
                "allow": [],
                "deny": [
                    "Bash(git commit:*)",
                    "Bash(git push:*)",
                    "Write(*)",
                    "Edit(*)",
                ],
            },
            "product_owner": {
                "allow": [
                    f"Write(/{workspace_path}/**)",
                    f"Edit(/{workspace_path}/**)",
                ],
                "deny": [],
            },
            "head_marketing": {
                "allow": [
                    f"Write(/{workspace_path}/**)",
                    f"Edit(/{workspace_path}/**)",
                ],
                "deny": [],
            },
            "auditor": {
                "allow": [],
                "deny": [
                    "Write(*)",
                    "Edit(*)",
                ],
            },
        }

        if role not in configs:
            logger.warning(
                "No Claude Code permissions configured for role; "
                "agent will be limited to base_allow/base_deny.",
                role=role,
            )
        return configs.get(role, {"allow": [], "deny": []})

    def _generate_agent_settings(
        self,
        agent_id: str,
        role: str,
        workspace_path: str,
        cell_workspace_path: str,
    ) -> Path:
        """Generate per-agent Claude Code settings file with role-specific permissions.

        This replaces the shared settings approach. Each agent gets their own
        settings.json with:
        - Base MCP tools allowed for all agents
        - Role-specific tool permissions
        - Explicit deny list blocking native git/file operations

        Args:
            agent_id: Agent identifier (e.g., "be-dev-1")
            role: Agent role (e.g., "developer")
            workspace_path: Path to agent's own workspace directory
            cell_workspace_path: Path to cell's workspace root (for QA/Docs)

        Returns:
            Path to the generated settings file
        """
        # Base MCP tools for all agents. Post-gateway every role gets the
        # full intent-verb + content-tool surface; the orchestrator-side
        # API rejects verbs/tools the agent's role isn't authorized for,
        # so granting `*` here is safe.
        base_allow = [
            "mcp__roboco-flow__*",
            "mcp__roboco-do__*",
            "mcp__roboco-optimal__*",
            "mcp__roboco-git-readonly__*",
            "Read(*)",  # All agents can read any file
        ]

        # Base denials for all agents - block native tools + sensitive reads.
        base_deny = [
            "Bash(git:*)",
            "Read(**/.git/config)",
            "Read(**/.gitconfig)",
            "Read(/etc/gitconfig)",
            "Read(~/.netrc)",
            "Read(**/.git-credentials)",
            "Bash(curl:*github.com*)",
            "Bash(curl:*api.github.com*)",
            "Bash(wget:*github.com*)",
            "Bash(wget:*api.github.com*)",
            "Bash(cat:*.git/config*)",
            "Bash(cat:*.gitconfig*)",
            "Bash(cat:*.git-credentials*)",
            "Bash(env:*)",
            "Bash(printenv:*)",
        ]

        # Get role-specific permissions
        role_config = self._get_role_permissions(
            role, workspace_path, cell_workspace_path
        )

        settings: dict[str, Any] = {
            "permissions": {
                "defaultMode": "bypassPermissions",
                "allow": base_allow + role_config["allow"],
                "deny": base_deny + role_config["deny"],
            },
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/sdk-startup-hook.sh",
                            }
                        ]
                    }
                ],
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/bash-guard-hook.sh",
                            }
                        ],
                    },
                ],
                "PostToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/a2a-check-hook.sh",
                            }
                        ],
                    },
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/post-tool-budget-hook.sh",
                            }
                        ],
                    },
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/usage-report-hook.sh",
                            }
                        ],
                    },
                ],
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/stop-hook.sh",
                            },
                            {
                                "type": "command",
                                "command": "/app/scripts/usage-report-hook.sh",
                            },
                        ]
                    }
                ],
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/user-prompt-hook.sh",
                            }
                        ]
                    }
                ],
                "PreCompact": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/pre-compact-hook.sh",
                            }
                        ]
                    }
                ],
                "SessionEnd": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/session-end-hook.sh",
                            }
                        ]
                    }
                ],
            },
        }

        # Write to per-agent settings file
        if DATA_HOST_PATH:
            settings_dir = Path("/app/agent-settings")
        else:
            settings_dir = Path(tempfile.gettempdir()) / "roboco-agent-settings"

        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path = settings_dir / f"{agent_id}-settings.json"

        # Handle case where Docker auto-created a directory instead of a file
        if settings_path.is_dir():
            shutil.rmtree(settings_path)

        settings_path.write_text(json.dumps(settings, indent=2))

        logger.debug(
            "Generated per-agent settings",
            agent_id=agent_id,
            role=role,
            settings_path=str(settings_path),
            allow_count=len(settings["permissions"]["allow"]),
            deny_count=len(settings["permissions"]["deny"]),
        )

        return settings_path
