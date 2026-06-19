"""roboco-intake MCP server — the Intake interviewer's ``propose_draft`` tool.

The grok-CLI interactive intake agent calls ``propose_draft`` once the task spec
is ready; this delivers the draft to the panel's reviewable draft card by POSTing
it straight to the prompter-live relay (the same ``/api/prompter/live/{session}/
events`` endpoint the driver's relay sink uses).

WHY IT POSTS DIRECTLY: grok's ``streaming-json`` output does not surface
tool-call events (verified live — a tool runs but never appears in the stream),
so :class:`~roboco.agent_sdk.grok_cli_session.GrokCliSession` cannot intercept
this call to emit a ``draft`` chunk. The tool POSTs the draft itself. (The Claude
intake path differs: the Claude SDK exposes the tool-use block, so its driver
intercepts it.)

Wired into ``~/.grok/config.toml`` by ``grok_intake_main``; the container
provides ``ROBOCO_API_URL`` + ``ROBOCO_PROMPTER_SESSION_ID``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

_TIMEOUT = 15.0

mcp = FastMCP("roboco-intake")


def _api_base() -> str:
    return os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000").rstrip(
        "/"
    )


async def post_draft(
    session_id: str,
    draft: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST the draft to the prompter-live relay; never raises.

    Module-level so it is unit-testable with ``httpx.MockTransport`` (the tool
    wrapper below only shapes the result string).
    """
    owns = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    url = f"{_api_base()}/api/prompter/live/{session_id}/events"
    try:
        resp = await http.post(
            url,
            json={
                "kind": "draft",
                "text": "",
                "tool": "propose_draft",
                "data": draft,
            },
        )
    except httpx.HTTPError as exc:
        return {"error": "request_failed", "detail": str(exc)}
    finally:
        if owns:
            await http.aclose()
    if not resp.is_success:
        return {"error": f"http_{resp.status_code}"}
    return {"ok": True}


@mcp.tool()
async def propose_draft(draft: dict[str, Any]) -> str:
    """Submit the finished task draft for the human to review and confirm.

    Call this once the spec is complete. Pass a JSON object: title, objective,
    what_this_builds[], the_work[] ({team, summary, items}), notes[],
    acceptance_criteria[], team, scale, task_type, nature, estimated_complexity,
    priority.
    """
    session_id = os.environ.get("ROBOCO_PROMPTER_SESSION_ID", "")
    if not session_id:
        return (
            "No live session id (ROBOCO_PROMPTER_SESSION_ID) — cannot surface the "
            "draft."
        )
    result = await post_draft(session_id, draft or {})
    if result.get("ok"):
        return "Draft submitted — the human can review it in the panel."
    detail = result.get("detail") or result.get("error") or "unknown error"
    return f"Could not submit the draft to the panel: {detail}"


if __name__ == "__main__":
    mcp.run()
