"""Secretary agent driver — SDK options + the CEO-authority tools.

The Secretary is a long-lived conversational agent like Intake; it reuses the
generic chat machinery (``IntakeDriver``, ``SdkIntakeSession``, ``normalize``)
and differs only in its tools. Where Intake has a single intercepted
``propose_draft``, the Secretary has three tools that actually call the backend
``/api/secretary/*`` routes on the CEO's behalf:

* ``read_company_state`` / ``read_task`` — reads (always allowed)
* ``submit_directive`` — acts; the backend gate-list queues high-impact kinds
  for the CEO's confirmation and runs low-risk ones directly.

The backend-calling logic lives in module-level helpers (``_do_*``) so it is
unit-testable with ``httpx.MockTransport``; ``build_secretary_options`` only
wraps them as SDK tools (the SDK construction itself is not gate-covered).
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

_TIMEOUT = 30.0
_SECRETARY_BASE_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")


def _api_base() -> str:
    return os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000").rstrip(
        "/"
    )


def _headers() -> dict[str, str]:
    headers = {
        "X-Agent-ID": os.environ.get("ROBOCO_AGENT_ID", ""),
        "X-Agent-Role": os.environ.get("ROBOCO_AGENT_ROLE", "secretary"),
    }
    token = os.environ.get("ROBOCO_AGENT_TOKEN")
    if token:
        headers["X-Agent-Token"] = token
    return headers


async def _call_backend(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Call ``/api/secretary{path}`` with the agent's auth; never raises."""
    owns = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        resp = await http.request(
            method,
            f"{_api_base()}/api/secretary{path}",
            headers=_headers(),
            json=json_body,
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return {"error": "request_failed", "detail": str(exc)}
    finally:
        if owns:
            await http.aclose()
    if not resp.is_success:
        return {"error": f"http_{resp.status_code}", "detail": resp.text[:300]}
    parsed: dict[str, Any] = resp.json()
    return parsed


async def _do_read_state(*, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    return await _call_backend("GET", "/state", client=client)


async def _do_read_task(
    task_id: str, *, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    return await _call_backend("GET", f"/tasks/{task_id}", client=client)


async def _do_submit_directive(
    kind: str,
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    return await _call_backend(
        "POST",
        "/directives",
        json_body={"kind": kind, "payload": payload},
        client=client,
    )


def _text_result(data: dict[str, Any]) -> dict[str, Any]:
    """Shape a backend result as an SDK tool text result."""
    return {"content": [{"type": "text", "text": json.dumps(data)}]}


def build_secretary_options(
    *,
    system_prompt: str,
    cwd: str,
    model: str | None = None,
) -> Any:  # pragma: no cover - thin SDK construction
    """Build locked-down ``ClaudeAgentOptions`` for the Secretary session.

    Same isolation as Intake (``strict_mcp_config`` + ``setting_sources=[]`` +
    a ``can_use_tool`` allowlist), but the MCP server exposes the Secretary's
    read + directive tools, which call the backend.
    """
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        PermissionResultAllow,
        PermissionResultDeny,
        create_sdk_mcp_server,
        tool,
    )

    @tool(
        "read_company_state",
        "Read a compact snapshot of company state: the charter (goals), task "
        "counts by status, pending pitches, and any directives awaiting the "
        "CEO's confirmation.",
        {},
    )
    async def _t_read_state(_args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(await _do_read_state())

    @tool("read_task", "Read one task's detail by its id.", {"task_id": str})
    async def _t_read_task(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(await _do_read_task(str(args["task_id"])))

    @tool(
        "submit_directive",
        "Act on the CEO's command. 'kind' is one of: relay_message "
        "(payload: channel, text), update_charter (payload: charter), "
        "control_task (payload: task_id, action[start|cancel|override], "
        "status?), approve_pitch (payload: pitch_id, notes?), announce "
        "(payload: text). High-impact kinds (charter, control_task, "
        "approve_pitch, announce) are queued for the CEO's explicit "
        "confirmation; relay_message runs directly.",
        {"kind": str, "payload": dict},
    )
    async def _t_submit(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(
            await _do_submit_directive(str(args["kind"]), dict(args.get("payload", {})))
        )

    server = create_sdk_mcp_server(
        name="secretary",
        version="1.0.0",
        tools=[_t_read_state, _t_read_task, _t_submit],
    )

    async def _gate(tool_name: str, _input: dict[str, Any], _ctx: Any) -> Any:
        if tool_name in _SECRETARY_BASE_TOOLS or "secretary__" in tool_name:
            return PermissionResultAllow()
        if tool_name == "AskUserQuestion" or tool_name.endswith("AskUserQuestion"):
            return PermissionResultDeny(
                message=(
                    "AskUserQuestion isn't available — just write your question "
                    "as a normal chat message; the CEO reads every reply live."
                )
            )
        if tool_name == "ExitPlanMode" or tool_name.endswith("ExitPlanMode"):
            return PermissionResultDeny(
                message="You don't use plan mode. Act via submit_directive."
            )
        return PermissionResultDeny(
            message=(
                f"{tool_name} is not available to the Secretary. Your tools are "
                "Read, Grep, Glob, read_company_state, read_task, and "
                "submit_directive."
            )
        )

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=cwd,
        mcp_servers={"secretary": server},
        allowed_tools=[
            *_SECRETARY_BASE_TOOLS,
            "mcp__secretary__read_company_state",
            "mcp__secretary__read_task",
            "mcp__secretary__submit_directive",
        ],
        model=model,
        include_partial_messages=True,
        permission_mode="dontAsk",
        strict_mcp_config=True,
        setting_sources=[],
        can_use_tool=_gate,
    )
