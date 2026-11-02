"""One locked-down Claude Haiku turn, run in a process of its own as the owner
of the mounted Claude Code login.

The background LLM client (``roboco.services.background_llm``) starts this
module as ``python -I -m roboco.agent_sdk.haiku_turn`` under the uid:gid that
owns the login directory, with no supplementary groups and ``HOME`` set to the
directory that holds the login. The orchestrator runs as root, and the Claude
Code CLI writes into the login (an OAuth refresh into ``.credentials.json``, a
``.claude.json`` backup, a built-in plugin record, ``sessions/`` entries): run
as root, each of those would land root-owned in the CEO's ``~/.claude``, which
the CEO's own CLI and every agent (uid 1000) then fail to rewrite.
claude-agent-sdk's own ``user`` option cannot prevent that: its subprocess
transport hands the value to ``Popen(user=...)``, which only calls
``setreuid`` and leaves the CLI in group 0 with root's supplementary groups.
Dropping the identity here, before the SDK starts, covers the CLI and the
SDK's own ``claude -v`` probe alike.

Protocol: one JSON :class:`HaikuTurnRequest` on stdin, one JSON
:class:`HaikuTurnOutcome` on stdout.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

_ERROR_DETAIL_MAX_CHARS = 300


@dataclass(frozen=True)
class HaikuTurnRequest:
    """Everything the turn needs; the parent resolves model, cap and cwd."""

    model: str
    user_prompt: str
    system_prompt: str
    output_cap_tokens: int
    cwd: str


@dataclass(frozen=True)
class HaikuTurnOutcome:
    """The turn's text, or why it produced none."""

    text: str | None = None
    error_detail: str | None = None
    api_error_status: int | None = None


def locked_down_haiku_options(request: HaikuTurnRequest) -> ClaudeAgentOptions:
    """The Claude Code options for one background Haiku turn.

    One turn, no built-in tools, no MCP server from any source, no settings
    file (so no hooks, plugins or permissions from the login directory), and
    ``--safe-mode`` on top, which turns off every customization (CLAUDE.md,
    skills, installed plugins, hooks, MCP servers) while auth works normally.
    ``--no-session-persistence`` keeps the transcript out of
    ``~/.claude/projects``. ``verbatim_prompts`` delivers the prompt as written,
    so an ``@/path`` inside untrusted text (a vault note, an X mention) cannot
    make the CLI read a local file into it. Haiku 5.5's thinking cannot be
    turned off (the CLI sends adaptive thinking whatever it is asked), so
    effort is ``low`` and the output cap carries thinking headroom.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        model=request.model,
        system_prompt=request.system_prompt,
        tools=[],
        mcp_servers={},
        strict_mcp_config=True,
        setting_sources=[],
        permission_mode="dontAsk",
        max_turns=1,
        cwd=request.cwd,
        verbatim_prompts=True,
        effort="low",
        env={
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(request.output_cap_tokens),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
        extra_args={"safe-mode": None, "no-session-persistence": None},
    )


def _outcome_of_result(result: ResultMessage | None) -> HaikuTurnOutcome:
    if result is None:
        return HaikuTurnOutcome(error_detail="no result message")
    if result.is_error:
        detail = "; ".join(result.errors or []) or (result.result or "")
        return HaikuTurnOutcome(
            error_detail=f"{result.subtype}: {detail}"[:_ERROR_DETAIL_MAX_CHARS],
            api_error_status=result.api_error_status,
        )
    return HaikuTurnOutcome(text=result.result)


async def run_haiku_turn(request: HaikuTurnRequest) -> HaikuTurnOutcome:
    """Run the turn through claude-agent-sdk; an SDK or CLI failure becomes an
    outcome with ``error_detail``, never an exception."""
    from claude_agent_sdk import ResultMessage, query

    final: ResultMessage | None = None
    try:
        options = locked_down_haiku_options(request)
        async for message in query(prompt=request.user_prompt, options=options):
            if isinstance(message, ResultMessage):
                final = message
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:_ERROR_DETAIL_MAX_CHARS]
        return HaikuTurnOutcome(error_detail=detail)
    return _outcome_of_result(final)


def main() -> None:
    request = HaikuTurnRequest(**json.load(sys.stdin))
    outcome = asyncio.run(run_haiku_turn(request))
    json.dump(asdict(outcome), sys.stdout)


if __name__ == "__main__":
    main()
