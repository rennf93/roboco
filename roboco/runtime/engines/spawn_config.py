"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: spawn_config)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import status as http_status

from roboco.agents.factories._base import compose_prompt
from roboco.agents_config import (
    get_agent_role,
    get_agent_team,
    get_escalation_target,
)
from roboco.config import settings
from roboco.models import AgentRole, Team
from roboco.runtime.orchestrator import (
    DATA_HOST_PATH,
    PROJECT_HOST_PATH,
    UUID_TO_SLUG,
    AgentConfig,
    _resolve_agent_cli_model,
    _system_api_headers,
    logger,
)
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    from roboco.models.runtime import (
        SpawnGitContext,
    )

    # Also bound into this module's real (non-TYPE_CHECKING) globals at
    # runtime by orchestrator.py, right after the class is defined -- see
    # its bottom-of-file comment.
    from roboco.runtime.orchestrator import (
        AgentOrchestrator,
    )


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class SpawnConfigEngine(_Base):
    """Mixin holding the "spawn_config" methods moved out of AgentOrchestrator."""

    def _get_role_permissions(
        self, role: str, workspace_path: str, cell_workspace_path: str
    ) -> dict[str, list[str]]:
        """Get role-specific allow/deny lists for Claude Code tools.

        Post-gateway shape: every state-changing operation an agent can
        perform routes through ``mcp__roboco-flow__*`` (intent verbs) or
        ``mcp__roboco-do__*`` (content tools — commit, push, PR, journal,
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
        # workspace_path: /data/workspaces/{project}/{team}/{agent}
        # cell_workspace_path: /data/workspaces/{project}/{team}
        configs: dict[str, dict[str, list[str]]] = {
            "developer": {
                "allow": [
                    f"Write(/{workspace_path}/**)",
                    f"Edit(/{workspace_path}/**)",
                ],
                "deny": [],
            },
            "qa": {
                # QA reads code + the open PR via the gateway; never edits.
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
                # PMs coordinate; they open + merge PRs through the gateway
                # but never author code. Edit/Write are denied so weaker
                # models can't read the subtask title imperatively and
                # start editing source — they have to decompose into a dev
                # subtask. Devs are the only role that authors code.
                "allow": [],
                "deny": [
                    "Bash(git commit:*)",
                    "Bash(git push:*)",
                    "Write(*)",
                    "Edit(*)",
                ],
            },
            "main_pm": {
                # Same reasoning as cell_pm — Main PM sits between CEO and
                # cell PMs; the work product is coordination + review, not
                # commits or edits. Code work routes Main PM → Cell PM →
                # Dev only.
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
                # Auditor is read-only across the org — observes, never edits.
                "allow": [],
                "deny": [
                    "Write(*)",
                    "Edit(*)",
                ],
            },
            "pr_reviewer": {
                # PR reviewer reads untrusted external/fork PR diffs and posts a
                # change-request via the gateway — it never writes files. Make the
                # read-only invariant explicit at the permission layer (it is the
                # highest-value prompt-injection target), not just implicit in the
                # absence of a writable mount.
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

    def _fable_hook_groups(self) -> dict[str, list[dict[str, Any]]]:
        """Additive Fable-mode hook registrations, keyed by Claude Code event.

        Empty when the flag is off, so callers that append these onto the
        existing per-event arrays leave settings.json byte-for-byte
        unchanged. Appended AFTER RoboCo's own hooks for each event —
        stop-hook.sh's mechanical terminal-verb check runs first, the
        Fable linguistic check runs second. See
        docs/superpowers/plans/2026-07-04-v0.18.0-A-opus-fable-plan.md.
        """
        from roboco.config import settings as _settings

        if not _settings.fable_mode_enabled:
            return {}
        return {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/app/scripts/fable-stop-gate-hook.sh",
                        }
                    ]
                },
            ],
            "SubagentStop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/app/scripts/fable-stop-gate-hook.sh subagent",
                        }
                    ]
                },
            ],
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/app/scripts/fable-bash-discipline-hook.sh",
                        }
                    ],
                },
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/app/scripts/fable-honesty-nudge-hook.sh",
                        }
                    ],
                },
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/app/scripts/fable-prompt-nudge-hook.sh",
                        }
                    ]
                },
            ],
            "PreCompact": [
                {
                    "matcher": "manual|auto",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/app/scripts/fable-precompact-hook.sh",
                        }
                    ],
                },
            ],
        }

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
        # The Read/Bash denies below are critical: without them an agent can
        # read `.git/config` (which, pre-fix, had the PAT embedded in the
        # remote URL) or `~/.gitconfig` and exfiltrate project secrets.
        # We also block direct curl/wget to github.com — any git-remote op
        # must go through the orchestrator's git service, which injects the
        # token via bearer header at subprocess time rather than exposing it.
        base_deny = [
            # Block ALL native git commands - must use roboco_git_* tools
            "Bash(git:*)",
            # Fleet-wide subagent ban (CEO, 2026-07-09): no agent spawns Claude
            # Code subagents. `Task` is a default-permitted built-in, so under
            # defaultMode=bypassPermissions the manifest/allowlist omission does
            # NOT remove it — only an explicit deny does. Mirrors the grok path's
            # `--disallowed-tools Agent`. `allows_subagent` is False for every
            # role, so this is unconditional.
            "Task",
            # NOTE: Write/Edit are intentionally NOT globally denied here.
            # Claude Code evaluates rules deny -> ask -> allow and the first
            # match wins, so a deny ALWAYS beats a more-specific allow (the
            # glob syntax has no negation). A global Write(*)/Edit(*) here
            # therefore unconditionally shadowed the per-role,
            # workspace-scoped Write/Edit allows below — every agent (devs
            # included) was unable to edit ANY file and fell back to
            # destructive bash redirection (clobbering real files). Roles
            # that must NOT write (qa, cell_pm, main_pm, auditor) carry
            # their own Write(*)/Edit(*) deny in _get_role_permissions.
            # Block reads of credential stores, anywhere on the FS
            "Read(**/.git/config)",
            "Read(**/.gitconfig)",
            "Read(/etc/gitconfig)",
            "Read(~/.netrc)",
            "Read(**/.git-credentials)",
            # The host's Claude Code OAuth credential store (`~/.claude`) is
            # bind-mounted read-write into EVERY agent container at
            # /home/agent/.claude (see _build_mount_args) — it is the shared
            # subscription auth every spawned agent uses, so it can't be
            # narrowed per-agent. Nothing in any role's job requires the LLM
            # to read its own harness's credentials, so block the Read tool
            # from the two files that carry them (`.credentials.json` on
            # Linux hosts without a keychain; `.claude.json` carries the
            # linked account + MCP trust state). Absolute `//` form per the
            # #167 gotcha above — a single `/` resolves against the
            # settings.json project root, not the container filesystem root.
            "Read(//home/agent/.claude/.credentials.json)",
            "Read(//home/agent/.claude.json)",
            # Block direct GitHub API/wire access — agents must use
            # roboco_git_* MCP tools so secrets + traceability stay on the
            # orchestrator side.
            "Bash(curl:*github.com*)",
            "Bash(curl:*api.github.com*)",
            "Bash(wget:*github.com*)",
            "Bash(wget:*api.github.com*)",
            # Same idea for cat-ing credential files in a subshell
            "Bash(cat:*.git/config*)",
            "Bash(cat:*.gitconfig*)",
            "Bash(cat:*.git-credentials*)",
            "Bash(cat:*.credentials.json*)",
            "Bash(cat:*.claude.json*)",
            # Block reading env vars that might leak secrets
            "Bash(env:*)",
            "Bash(printenv:*)",
        ]

        # Get role-specific permissions
        role_config = self._get_role_permissions(
            role, workspace_path, cell_workspace_path
        )

        # Combine base + role-specific.
        # defaultMode=bypassPermissions lets unlisted operations proceed
        # without an interactive prompt (which would hang a non-TTY agent
        # container). Explicit deny rules still apply.
        settings: dict[str, Any] = {
            # Agent commits carry the agent's own identity, never the model
            # vendor's — without this the CLI's default nudges the model into
            # appending "Co-Authored-By: Claude <noreply@anthropic.com>" to
            # commit messages it hands the gateway commit verb.
            "includeCoAuthoredBy": False,
            "permissions": {
                "defaultMode": "bypassPermissions",
                "allow": base_allow + role_config["allow"],
                "deny": base_deny + role_config["deny"],
            },
            # Explicit Bash-output cap: a gate/test dump enters the session
            # context once and is re-read at cache-read price on every later
            # turn. 20K chars (~5K tokens) keeps failures diagnosable without
            # relying on the CLI's default ceiling.
            "env": {
                "BASH_MAX_OUTPUT_LENGTH": "20000",
            },
            "hooks": {
                # Start SDK server on session start (for A2A communication)
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
                # Guard Bash: block shell-level git/curl/wget/env patterns
                # that the matcher-based `permissions.deny` can't catch
                # (e.g. `cd X && git fetch`). Redirects agents to the MCP
                # equivalents instead of bloating prompts with rules.
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
                    # Check for incoming A2A messages after each tool use
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/a2a-check-hook.sh",
                            }
                        ],
                    },
                    # Per-session budget counter + loop detector. Shared SDK
                    # state lets this hook emit [Budget]/[Loop]/[Halt]
                    # reminders that the orchestrator's kill-switch corroborates.
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/post-tool-budget-hook.sh",
                            }
                        ],
                    },
                    # Sync token usage from the transcript so /usage/status
                    # (and the cost dashboard) reflect real spend. Idempotent
                    # absolute set — running it per tool keeps mid-run
                    # snapshots and reaped-agent sessions accurate.
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
                # Stop guard: refuse silent exits unless a terminal tool was
                # just called (idle/substitute/escalate/pause/...). Second
                # attempt auto-substitutes via SDK so the task doesn't rot.
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/stop-hook.sh",
                            },
                            # Final token-usage sync at turn end — guarantees
                            # the session total is captured before the agent
                            # idles and the orchestrator finalizes the row.
                            {
                                "type": "command",
                                "command": "/app/scripts/usage-report-hook.sh",
                            },
                        ]
                    }
                ],
                # Prompt-injection guard — rejects turns that look like
                # another agent's content trying to override our rules.
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
                # Snapshot budget / terminal state before compact so the
                # next session resumes with continuity.
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
                # Post-mortem: write a reflect-journal entry summarising the
                # session (tools called, halt/loop triggered, last tool).
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

        for event, groups in self._fable_hook_groups().items():
            settings["hooks"].setdefault(event, []).extend(groups)

        # Write to per-agent settings file
        # When running in container: write to /app/agent-settings (mounted to host)
        # When running on host: use temp directory
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

    @staticmethod
    def _default_spawn_prompt() -> str:
        """Fallback prompt when the caller provided none."""
        return (
            "You may have been spawned without a specific task assignment. "
            "Follow your standard workflow:\n\n"
            "1. Call `give_me_work()` to find work for your role\n"
            "2. Begin the assigned task (its details arrive in the "
            "response): UNDERSTAND -> PLAN -> EXECUTE -> VERIFY -> HANDOFF\n"
            "3. If no tasks available, call `i_am_idle()` "
            "to shutdown gracefully\n\n"
            "Start now by scanning for work."
        )

    @staticmethod
    def _resolve_cli_model(config: AgentConfig) -> str:
        """Return the string to pass to `claude --model`."""
        return _resolve_agent_cli_model(config.provider_type, config.model)

    async def _generate_mcp_config(
        self,
        agent_id: str,
        git_context: SpawnGitContext | None = None,
        task_id: str | None = None,
    ) -> Path:
        """Generate MCP config for an agent.

        Post-gateway: every state-changing tool routes through one of two
        servers, and read-only views go through two more:

        - roboco-flow         intent verbs (lifecycle transitions)
        - roboco-do           content tools (commit, push, PR, journal,
                              notify, message)
        - roboco-git-readonly status, log, diff, branch list
        - roboco-optimal      knowledge base, RAG, semantic search
        - roboco-docs         documentation file management (panel docs)
        - playwright          browser tools (fe-qa/ux-qa, the ux-dev on
                              a video-authoring task, and the PO on a
                              dogfood-walk task — see below)

        The agent's role is asserted by the orchestrator API on every
        verb/tool call, so all roles get the same MCP surface from this
        registration; verbs the agent's role can't run return a
        not-authorized error rather than 404. Git context is forwarded
        only as a fallback for tools that resolve project/branch from env.
        """
        # MCP servers run inside agent containers, need to connect to the
        # orchestrator API. Prefer an explicit settings.api_url override —
        # production sets it to the container hostname, and the eval harness
        # patches it to its disposable in-process stack (see runner.py's
        # _bench_environment) so a spawned container's MCP servers resolve to
        # the throwaway orchestrator, never the real production one. Fall back
        # to the PROJECT_HOST_PATH / settings.port logic when it is unset.
        if settings.api_url:
            api_url = settings.api_url
        elif PROJECT_HOST_PATH:
            api_url = "http://roboco-orchestrator:8000"
        else:
            api_url = f"http://127.0.0.1:{settings.port}"

        agent_role = get_agent_role(agent_id) or ""
        # Gateway v1 endpoints declare X-Agent-ID as Annotated[UUID, Header(...)],
        # so the MCP server has to forward the agent's UUID — not the slug — or
        # every gateway call 422s on header parse. Resolve via AGENT_UUIDS map;
        # if the slug isn't in the map (custom agents), fall back to the slug
        # and let the API surface the unknown-agent error.
        # Also used as the CLI arg for the three ApiClient-based servers
        # (optimal/docs/search) below — their spawn token (issue_agent_token)
        # is signed over the UUID, so ApiClient's X-Agent-ID must match or
        # verify_agent_token 401s with "signature mismatch" even though
        # get_agent_role/get_agent_team resolve either form fine.
        agent_uuid = AGENT_UUIDS.get(agent_id, agent_id)

        mcp_env: dict[str, str] = {
            "ROBOCO_API_URL": api_url,
            "ROBOCO_ORCHESTRATOR_URL": api_url,
            "ROBOCO_AGENT_ID": agent_uuid,
            "ROBOCO_AGENT_ROLE": agent_role,
            # Mirrors the server-side FlowVerbTimeoutMiddleware budgets so the
            # roboco-flow MCP client's per-verb timeout (flow_server.py, which
            # can't read Settings directly) stays coherent with operator
            # tuning of either setting.
            "ROBOCO_FLOW_VERB_TIMEOUT_SECONDS": str(settings.flow_verb_timeout_seconds),
            "ROBOCO_FLOW_VERB_SLOW_TIMEOUT_SECONDS": str(
                settings.flow_verb_slow_timeout_seconds
            ),
            # Every MCP server is launched as `uv run python -m
            # roboco.mcp.<server>` by Claude Code, with cwd = the agent's
            # WORKSPACE (not /app). Without this, `uv run` resolves a
            # cwd-relative `.venv` (≠ the pre-baked /app/.venv) and RE-SYNCS
            # the full dependency set (torch/lancedb/pyarrow/scipy, ~350MB) into a
            # fresh venv on every spawn — masked by a warm uv wheel cache,
            # but on a cold cache (first spawn after an image rebuild) the
            # download takes minutes and the MCP servers never come up
            # before the agent burns its budget. Pinning the project env
            # to the pre-baked venv is necessary but NOT sufficient: `uv run`
            # still resolves the project from the workspace cwd and re-syncs
            # when the clone's uv.lock drifts from the image — leaving the MCP
            # servers stuck at status="pending" so the agent gets zero gateway
            # verbs. Each server is therefore launched with `uv run --no-sync`
            # (below) to use /app/.venv as-is and start instantly.
            "UV_PROJECT_ENVIRONMENT": "/app/.venv",
            # cwd is the agent's workspace (often a RoboCo clone). Without
            # this, cwd lands on sys.path[0] and a stale/foreign clone's
            # `roboco` package shadows the image's installed one in
            # /app/.venv, crashing the server on import (e.g. an old clone
            # still importing a since-removed mcp.server.fastmcp symbol).
            "PYTHONSAFEPATH": "1",
        }

        # Add git context if available
        if git_context:
            if git_context.project_slug:
                mcp_env["ROBOCO_PROJECT_SLUG"] = git_context.project_slug
            if git_context.branch_name:
                mcp_env["ROBOCO_BRANCH"] = git_context.branch_name

        mcp_servers: dict[str, dict[str, Any]] = {
            # Intent verbs — every role-scoped lifecycle transition.
            "roboco-flow": {
                "command": "uv",
                "args": ["run", "--no-sync", "python", "-m", "roboco.mcp.flow_server"],
                "env": mcp_env,
            },
            # Content tools — commit, push, PR, journal, notify, message.
            "roboco-do": {
                "command": "uv",
                "args": ["run", "--no-sync", "python", "-m", "roboco.mcp.do_server"],
                "env": mcp_env,
            },
            # Read-only git views — status, log, diff, branches.
            "roboco-git-readonly": {
                "command": "uv",
                "args": ["run", "--no-sync", "python", "-m", "roboco.mcp.git_readonly"],
                "env": mcp_env,
            },
            # Knowledge base — RAG / semantic search / ask_mentor.
            "roboco-optimal": {
                "command": "uv",
                "args": [
                    "run",
                    "--no-sync",
                    "python",
                    "-m",
                    "roboco.mcp.optimal_server",
                    agent_uuid,
                ],
                "env": mcp_env,
            },
        }

        video_authoring = await self._is_video_authoring_spawn(
            agent_id, agent_role, task_id
        )
        dogfood_walk = await self._is_dogfood_spawn(agent_id, agent_role, task_id)
        self._append_role_scoped_mcp_servers(
            mcp_servers,
            agent_id,
            agent_role,
            agent_uuid,
            mcp_env,
            video_authoring=video_authoring,
            dogfood_walk=dogfood_walk,
        )

        config: dict[str, Any] = {"mcpServers": mcp_servers}

        # Write to shared config directory (mounted in both orchestrator and agents)
        # When running in container: /app/mcp-configs -> host's ./data/mcp-configs
        # When running on host: use temp directory
        if DATA_HOST_PATH:
            # Running in container - use shared mounted directory
            config_dir = Path("/app/mcp-configs")
            config_dir.mkdir(parents=True, exist_ok=True)
        else:
            # Running on host - use temp directory
            config_dir = Path(tempfile.gettempdir())

        # basename-sanitized like _grok_usage_json: agent ids are orchestrator-
        # issued slugs, but the filename must not be able to traverse anyway.
        safe_agent_id = os.path.basename(agent_id)
        config_path = config_dir / f"roboco-mcp-{safe_agent_id}.json"
        config_path.write_text(json.dumps(config, indent=2))

        return config_path

    def _append_role_scoped_mcp_servers(
        self,
        mcp_servers: dict[str, dict[str, Any]],
        agent_id: str,
        agent_role: str,
        agent_uuid: str,
        mcp_env: dict[str, str],
        *,
        video_authoring: bool = False,
        dogfood_walk: bool = False,
    ) -> None:
        """Register the role-scoped MCP servers (docs, research, playwright).

        Split from ``_generate_mcp_config`` so the base registration stays
        within the complexity budget; each branch is fail-closed server-side
        regardless of registration.
        """
        # Docs server — documentation file management. Registered only for
        # roles that touch panel docs; handlers still enforce per-role
        # access so the surface is fail-closed.
        docs_roles = (
            "documenter",
            "cell_pm",
            "main_pm",
            "product_owner",
            "head_marketing",
        )
        if agent_role in docs_roles:
            mcp_servers["roboco-docs"] = {
                "command": "uv",
                "args": [
                    "run",
                    "--no-sync",
                    "python",
                    "-m",
                    "roboco.mcp.docs_server",
                    agent_uuid,
                ],
                "env": mcp_env,
            }

        # Web research — external search/fetch for Board + PM roles. The
        # provider key stays server-side (the route holds it); the agent only
        # ever talks to the backend, so the container needs no external egress.
        research_roles = (
            "cell_pm",
            "main_pm",
            "product_owner",
            "head_marketing",
        )
        if settings.research_enabled and agent_role in research_roles:
            mcp_servers["roboco-search"] = {
                "command": "uv",
                "args": [
                    "run",
                    "--no-sync",
                    "python",
                    "-m",
                    "roboco.mcp.search_server",
                    agent_uuid,
                ],
                "env": mcp_env,
            }

        # Playwright MCP — structured browser tools (navigate/click/snapshot/
        # screenshot). Three cases: fe-qa/ux-qa browser verification, a
        # ux-dev authoring a source=video task (``video_authoring``, probed
        # at spawn) so the composition author can preview their HTML in a
        # real browser while iterating, and a product_owner spawned onto a
        # source=board_dogfood task (``dogfood_walk``, probed at spawn via
        # ``_is_dogfood_spawn``) so the PO can walk the panel as a user —
        # task-scoped, not role-blanket: a PO spawned for any other board
        # program must NOT get browser tools. All three run images that bake
        # the binary + wrapper entrypoint (docker/agent-qa-fe.Dockerfile,
        # docker/agent-ux.Dockerfile, docker/agent-pm.Dockerfile); registering
        # it for any other role would reference a command that doesn't exist
        # in that image.
        playwright_mcp_teams = ("frontend", "ux_ui")
        browser_qa = (
            agent_role == "qa" and get_agent_team(agent_id) in playwright_mcp_teams
        )
        if browser_qa or video_authoring or dogfood_walk:
            mcp_servers["playwright"] = {
                "command": "/app/scripts/playwright-mcp-entrypoint.sh",
                "args": [],
            }

    def _generate_composed_prompt(
        self, agent_id: str, ambient: str | None = None
    ) -> Path:
        """Generate composed system prompt for an agent.

        Uses the layered prompt composition system:
        base.md + roles/{role}.md + teams/{team}.md + identities/{agent}.md
        plus an optional ``ambient`` layer (the project's architectural
        standard, resolved by the async spawn path).

        Returns:
            Path to the generated prompt file
        """
        # Get role and team from canonical config
        role_str = get_agent_role(agent_id)
        team_str = get_agent_team(agent_id)

        # Convert to enums
        role_enum = AgentRole(role_str) if role_str else None
        team_enum = Team(team_str) if team_str else None

        if not role_enum:
            raise ValueError(f"Unknown role for agent: {agent_id}")

        # Compose the prompt from layers
        prompt_content = compose_prompt(role_enum, team_enum, agent_id, ambient=ambient)

        # Determine output directory
        if PROJECT_HOST_PATH:
            # Running in container - use shared directory that maps to host
            config_dir = Path("/app/prompts-generated")
            config_dir.mkdir(parents=True, exist_ok=True)
        else:
            # Running directly on host
            config_dir = Path(tempfile.gettempdir()) / "roboco-prompts"
            config_dir.mkdir(parents=True, exist_ok=True)

        # Write to file
        prompt_path = config_dir / f"{agent_id}-prompt.md"
        prompt_path.write_text(prompt_content)

        logger.debug(
            "Generated composed prompt",
            agent_id=agent_id,
            role=role_str,
            team=team_str,
            path=str(prompt_path),
            size=len(prompt_content),
        )

        return prompt_path

    async def _resolve_conventions_ambient(
        self,
        project_slug: str | None,
        task_id: str | None = None,
        product_id: str | None = None,
        project_ids: list[str] | None = None,
    ) -> str | None:
        """Resolve the architectural-standard ambient block for the spawn.

        Covers a delivery role's single project (via ``project_slug``), a PO /
        Intake working a product (per-cell projects resolved from the task's
        ``product_id`` or a directly-supplied ``product_id``), AND a MegaTask
        intake's explicit ``project_ids`` scope. Best-effort + flag-gated:
        returns None (no ambient layer) when the subsystem is off, no project is
        in scope, or anything fails — a prompt compose must never be blocked by
        conventions resolution.
        """
        from roboco.config import settings

        if not settings.conventions_enabled:
            return None
        try:
            from roboco.agents.factories._base import conventions_ambient_layer
            from roboco.db.base import get_session_factory

            factory = get_session_factory()
            async with factory() as db:
                projects = await self._resolve_ambient_projects(
                    db,
                    project_slug=project_slug,
                    task_id=task_id,
                    product_id=product_id,
                    project_ids=project_ids,
                )
                return await conventions_ambient_layer(db, projects)
        except Exception as exc:
            logger.warning(
                "Conventions ambient resolution failed (non-fatal)",
                project_slug=project_slug,
                error=str(exc),
            )
            return None

    async def _resolve_ambient_projects(
        self,
        db: Any,
        *,
        project_slug: str | None,
        task_id: str | None,
        product_id: str | None,
        project_ids: list[str] | None = None,
    ) -> list[Any]:
        """The in-scope projects for the ambient block: single repo, product,
        an explicit MegaTask ``project_ids`` set, or an ad-hoc cell map."""
        if project_ids:
            return await self._projects_by_ids(db, project_ids)
        if product_id is not None:
            return await self._ambient_product_projects(db, product_id)
        if task_id is not None:
            projects = await self._ambient_projects_for_task(db, task_id)
            if projects:
                return projects
        if project_slug:
            from roboco.services.project import get_project_service

            project = await get_project_service(db).get_by_slug(project_slug)
            return [project] if project is not None else []
        return []

    @staticmethod
    async def _projects_by_ids(db: Any, project_ids: list[str]) -> list[Any]:
        """Resolve an explicit id list to project rows, in order, skipping any
        that don't resolve — best-effort ambient resolution, not the hard
        clone-scope resolver (which fails loud on a missing id)."""
        from uuid import UUID

        from roboco.services.project import get_project_service

        project_svc = get_project_service(db)
        out = []
        for pid in project_ids:
            p = await project_svc.get(UUID(pid))
            if p is not None:
                out.append(p)
        return out

    @staticmethod
    async def _ambient_projects_for_task(db: Any, task_id: str) -> list[Any]:
        """The in-scope projects for a task's ambient block, from its product OR
        its ad-hoc ``cell_projects`` map. Empty for a plain project task (the
        project_slug branch handles those) or a not-yet-mapped coordination root.
        """
        from uuid import UUID

        from roboco.services.project import get_project_service
        from roboco.services.task import get_task_service

        task = await get_task_service(db).get(UUID(task_id))
        if task is None:
            return []
        project_service = get_project_service(db)
        if task.product_id is not None:
            from roboco.services.product import get_product_service

            ids = await get_product_service(db).distinct_project_ids(
                UUID(str(task.product_id))
            )
            resolved = [await project_service.get(pid) for pid in ids]
            return [p for p in resolved if p is not None]
        # Ad-hoc per-cell map: resolve the distinct projects the map spans (de-dupe
        # by project_id — a monorepo mapped across cells yields one project).
        distinct_ids: dict[Any, None] = {}
        for mapping in sorted(task.cell_projects, key=lambda m: m.team.value):
            distinct_ids.setdefault(UUID(str(mapping.project_id)), None)
        resolved = [await project_service.get(pid) for pid in distinct_ids]
        return [p for p in resolved if p is not None]

    @staticmethod
    async def _ambient_product_projects(db: Any, product_id: str) -> list[Any]:
        from uuid import UUID

        from roboco.services.product import get_product_service
        from roboco.services.project import get_project_service

        project_service = get_project_service(db)
        ids = await get_product_service(db).distinct_project_ids(UUID(product_id))
        resolved = [await project_service.get(pid) for pid in ids]
        return [p for p in resolved if p is not None]

    def _build_tool_load_block(self, role: str) -> str:
        """Briefing block affirming the role's built-in tools are ready.

        Built-in tools are pre-loaded at spawn via the Claude Code
        `--tools` flag and gated only by the per-role permission rules.
        ToolSearch is MCP-only and never gates built-ins — an earlier
        revision instructed agents to "run ToolSearch to activate
        Edit/Write", which was false (ToolSearch is not even callable
        here), so weak models chased a nonexistent tool and fell back to
        destructive shell file-writes. This states the tools are live and
        steers away from that failure. Cached per role.
        """
        if role in self._TOOL_LOAD_CACHE:
            return self._TOOL_LOAD_CACHE[role]
        tools = self._ROLE_BUILTIN_TOOLS.get(role)
        if not tools:
            block = ""
        else:
            tool_list = ", ".join(tools)
            edit_line = (
                "Make file changes with Edit/Write — never rewrite a "
                "whole file via shell redirection (>, heredoc, tee); "
                "that destroys content and is unnecessary.\n"
                if "Edit" in tools
                else "You read and review; you do not author files.\n"
            )
            block = (
                "## Your tools are ready\n"
                "\n"
                f"Loaded and available now: {tool_list}. Use them "
                "directly. Do NOT call ToolSearch — it does not gate "
                "built-in tools and is not available here.\n"
                f"{edit_line}"
                "\n"
            )
        self._TOOL_LOAD_CACHE[role] = block
        return block

    @staticmethod
    def _description_body(description: str | None, *, cap: int = 4000) -> str:
        """The task description as a bounded body for a prompt/briefing block.

        The description is the actual ask — it travels with the spawn prompt and
        SessionStart briefing so the dev starts with the spec instead of a bare
        title. Capped so a giant umbrella description can't swamp the prompt;
        the full upstream chain is still available via ``evidence()``.
        """
        text = (description or "").strip()
        if not text:
            return "(none — ask the PM before proceeding)"
        if len(text) <= cap:
            return text
        omitted = len(text) - cap
        return (
            f"{text[:cap]}\n… [{omitted} chars omitted — evidence() carries"
            " the full text]"
        )

    @staticmethod
    def _format_task_briefing_block(task_id: str, task: dict[str, Any]) -> str:
        """Build the ``## Current task`` markdown block from a fetched task."""
        criteria_list = task.get("acceptance_criteria") or []
        if isinstance(criteria_list, str):
            criteria_list = [criteria_list]
        criteria = (
            "\n".join(f"- {c}" for c in criteria_list)
            if criteria_list
            else "- (none listed — ask PM before proceeding)"
        )
        branch = task.get("branch_name") or "(to be created)"
        project_slug = task.get("project_slug") or "(unset — ask PM)"
        description_body = AgentOrchestrator._description_body(
            task.get("description") or ""
        )
        return (
            "\n## Current task\n"
            f"- **ID:** `{task.get('id', task_id)}`\n"
            f"- **Title:** {task.get('title', '(untitled)')}\n"
            f"- **Status:** {task.get('status', 'unknown')}\n"
            f"- **Type:** {task.get('task_type', 'unknown')}\n"
            f"- **Project slug:** `{project_slug}` "
            "(pass this as `project_slug=` on every git/task tool)\n"
            f"- **Branch:** `{branch}`\n"
            "\n### Description (the ask — treat as ground truth)\n"
            f"{description_body}\n"
            "\n### Acceptance criteria\n"
            f"{criteria}\n"
        )

    async def _fetch_task_for_briefing(
        self, agent_id: str, task_id: str
    ) -> dict[str, Any] | None:
        """Best-effort GET /tasks/{id}; returns task dict or None on failure."""
        try:
            async with httpx.AsyncClient(
                timeout=5.0, headers=_system_api_headers()
            ) as client:
                resp = await client.get(f"{self._api_url}/tasks/{task_id}")
            if resp.status_code == http_status.HTTP_200_OK:
                payload: dict[str, Any] = resp.json()
                return payload
        except Exception as e:
            logger.debug(
                "Briefing task-fetch failed — falling back to role-only",
                agent_id=agent_id,
                task_id=task_id,
                error=str(e),
            )
        return None

    async def _write_agent_briefing(
        self,
        agent_id: str,
        task_id: str | None,
        workspace_path: str,
        sandbox_services: list[str] | None = None,
    ) -> Path | None:
        """Write a compact task briefing to be read by SessionStart hook.

        The briefing saves the agent from burning its first 2-3 tool calls on
        `give_me_work` (whose Envelope already carries the task details). If
        `task_id` is known we fetch
        the task and include title, status, branch, and acceptance criteria.
        On fetch failure we still emit the role-level part (role, escalation
        target, terminal tools, workspace path) — strictly better than nothing.

        `sandbox_services` (when non-empty) names the request_sandbox verb so
        an opted-in project's agent knows it will succeed, rather than relying
        on manifest presence alone to discover it.
        """
        role = get_agent_role(agent_id) or "agent"
        team = get_agent_team(agent_id) or "-"
        escalate_to = get_escalation_target(agent_id) or "main-pm"

        tool_load_block = self._build_tool_load_block(role)
        task_block = ""
        if task_id:
            task = await self._fetch_task_for_briefing(agent_id, task_id)
            if task is not None:
                task_block = self._format_task_briefing_block(task_id, task)
        sandbox_line = (
            f"- **Sandbox available:** `{', '.join(sandbox_services)}` — call "
            "`request_sandbox()` to provision on demand\n"
            if sandbox_services
            else ""
        )

        content = (
            f"# Session briefing — {agent_id}\n"
            "\n"
            f"{tool_load_block}"
            "## You are\n"
            f"- **Agent:** `{agent_id}`\n"
            f"- **Role:** {role}\n"
            f"- **Team:** {team}\n"
            f"- **Escalate to:** `{escalate_to}`\n"
            f"- **Workspace:** `{workspace_path}`\n"
            f"{sandbox_line}"
            f"{task_block}"
            "\n## Terminal tools (how to exit cleanly)\n"
            "- `i_am_idle()` — no work remaining (every role)\n"
            "- `i_am_blocked(task_id, reason, ...)` — stuck (developer)\n"
            "- `unclaim(task_id)` — release a claim back to the pool\n"
            "- Role handoffs:\n"
            "  - developer → `i_am_done(task_id, notes)` (submit for QA)\n"
            "  - qa → `pass(task_id, notes)` / `fail(task_id, issues)`\n"
            "  - documenter → `i_documented(task_id, notes, files)`\n"
            "  - cell_pm → `complete(task_id, notes)` / `submit_up(...)`"
            " / `escalate_up(...)`\n"
            "  - main_pm → `complete(...)` / `escalate_to_ceo(...)`\n"
            "\n"
            "A Stop without a terminal tool will be rejected; a second Stop\n"
            "auto-substitutes the task so it can be picked up elsewhere.\n"
            "\n"
            "## Budget\n"
            f"Soft-warn at {settings.agent_tool_call_warn} tool calls, "
            f"hard cap at {settings.agent_tool_call_halt}. Loops — same "
            f"tool+args {settings.agent_loop_threshold}x within "
            f"{settings.agent_loop_window} calls — are flagged; stop and "
            "escalate instead of retrying.\n"
        )

        if PROJECT_HOST_PATH:
            briefings_dir = Path("/app/briefings")
        else:
            briefings_dir = Path(tempfile.gettempdir()) / "roboco-briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)

        path = briefings_dir / f"{agent_id}.md"
        path.write_text(content)
        logger.debug(
            "Wrote agent briefing",
            agent_id=agent_id,
            path=str(path),
            has_task=bool(task_block),
        )
        return path

    def _resolve_agent_slug(self, agent_id_or_uuid: str) -> str:
        """Resolve agent UUID to slug. Returns input if already a slug."""
        # Check if it's a known UUID and convert to slug
        if agent_id_or_uuid in UUID_TO_SLUG:
            return UUID_TO_SLUG[agent_id_or_uuid]
        # Already a slug or unknown UUID
        return agent_id_or_uuid

    def _mark_task_handled(self, task_id: str | None) -> None:
        """Record that `task_id` was acted on earlier in this dispatch tick."""
        if task_id:
            self._tick_handled_tasks.add(task_id)
