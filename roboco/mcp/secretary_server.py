"""roboco-secretary MCP server — the Secretary's CEO-authority tools.

Parity with the Claude Secretary's SDK tools
(:func:`roboco.agent_sdk.secretary_driver.build_secretary_options`):
``read_company_state`` / ``read_task`` (reads) and ``submit_directive`` (acts).
Each calls the backend ``/api/secretary/*`` routes with the container's HMAC
agent token; the backend gate-list queues high-impact directive kinds for the
CEO's confirmation and runs low-risk ones directly. The backend-calling logic is
reused verbatim from ``secretary_driver`` (the SDK and grok paths share one
HTTP seam), so this server only wraps those helpers as MCP tools.

Wired into ``~/.grok/config.toml`` by ``grok_secretary_main``; the container
provides ``ROBOCO_API_URL`` / ``ROBOCO_AGENT_ID`` / ``ROBOCO_AGENT_ROLE`` /
``ROBOCO_AGENT_TOKEN`` (the same auth substrate the one-shot Grok path uses).
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from roboco.agent_sdk.secretary_driver import (
    _do_read_state,
    _do_read_task,
    _do_submit_directive,
)

mcp = FastMCP("roboco-secretary")


@mcp.tool()
async def read_company_state() -> str:
    """Read a compact snapshot of company state.

    The charter (goals), task counts by status, pending pitches, and any
    directives awaiting the CEO's confirmation.
    """
    return json.dumps(await _do_read_state())


@mcp.tool()
async def read_task(task_id: str) -> str:
    """Read one task's full detail by its id — content, notes, plan,
    progress, and PR reference (Secretary FULL task access)."""
    return json.dumps(await _do_read_task(task_id))


@mcp.tool()
async def submit_directive(kind: str, payload: dict[str, Any]) -> str:
    """Act on the CEO's command.

    'kind' is one of: relay_message (payload: channel, text), update_charter
    (payload: charter), control_task (payload: task_id, action[start|cancel|
    override|edit], status? for override, fields? for edit — edit accepts
    title/description/acceptance_criteria/priority/team/estimated_complexity/
    nature/assigned_to; assigned_to may be a UUID or an agent slug like
    "be-dev-1"), approve_pitch (payload: pitch_id, notes?), announce
    (payload: text). High-impact kinds (charter, control_task, approve_pitch,
    announce) are queued for the CEO's explicit confirmation; relay_message runs
    directly.
    """
    return json.dumps(await _do_submit_directive(kind, payload or {}))


if __name__ == "__main__":
    mcp.run()
