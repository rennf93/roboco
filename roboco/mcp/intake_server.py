"""roboco-intake MCP server — the Intake interviewer's ``propose_draft`` and
``propose_batch`` (MegaTask) tools.

The grok-CLI interactive intake agent calls ``propose_draft`` (one task) or
``propose_batch`` (a sequenced MegaTask of several tasks) once the spec is ready;
this delivers it to the panel's reviewable card by POSTing it straight to the
prompter-live relay (the same ``/api/prompter/live/{session}/events`` endpoint the
driver's relay sink uses).

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

import json
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


async def _post_event(
    session_id: str,
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST one relay event to the prompter-live relay; never raises."""
    owns = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    url = f"{_api_base()}/api/prompter/live/{session_id}/events"
    try:
        resp = await http.post(url, json=payload)
    except httpx.HTTPError as exc:
        return {"error": "request_failed", "detail": str(exc)}
    finally:
        if owns:
            await http.aclose()
    if not resp.is_success:
        # Capture the relay's body so the caller can surface the real reason
        # (e.g. 'session not in MegaTask scope' on a 422) instead of an opaque
        # `http_422` token with no remediation (#57). None when the body is
        # absent or not JSON.
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            body = None
        return {"error": f"http_{resp.status_code}", "detail": body}
    return {"ok": True}


async def post_draft(
    session_id: str,
    draft: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST a single draft to the prompter-live relay; never raises.

    Module-level so it is unit-testable with ``httpx.MockTransport`` (the tool
    wrapper below only shapes the result string).
    """
    return await _post_event(
        session_id,
        {"kind": "draft", "text": "", "tool": "propose_draft", "data": draft},
        client=client,
    )


async def post_batch(
    session_id: str,
    batch: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST a MegaTask batch (``{drafts: [...], title}``) to the relay; never raises."""
    return await _post_event(
        session_id,
        {"kind": "batch", "text": "", "tool": "propose_batch", "data": batch},
        client=client,
    )


@mcp.tool()
async def propose_draft(draft: dict[str, Any]) -> str:
    """Submit the finished task draft for the human to review and confirm.

    Call this once the spec is complete. Pass a JSON object: title, objective,
    what_this_builds[], the_work[] ({team, summary, items, project_id}), notes[],
    acceptance_criteria[], team, scale, task_type, nature, estimated_complexity,
    priority. A multi-cell task (be+fe, fe+uxui) puts one entry per cell in
    the_work, each with its cell's project_id (the per-cell repo); the system
    builds the cell->project map from them.

    Sequenced batch intake (when enabled): if the CEO asks for several tasks at
    once, propose one draft per item and set each item's collision surface so the
    system can sequence them into conflict-free waves — intends_to_touch[] (the
    files/dirs this item will modify, from its grounding), adds_migration (does it
    add a DB migration / column?), touches_shared (does it edit a widely-shared
    component, token, or primitive?). Over-declaring a surface is safer than
    under-declaring; the analyzer derives the ordering from these.
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


def _draft_title(d: dict[str, Any]) -> str | None:
    """A draft is well-formed when it carries a string ``title`` OR ``name``."""
    t = d.get("title")
    if isinstance(t, str):
        return t
    n = d.get("name")
    if isinstance(n, str):
        return n
    return None


def _normalize_batch_drafts(
    raw: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Filter + normalize MegaTask drafts for the panel relay (#163).

    A draft without a string ``title``/``name`` is dropped and counted. A
    ``name``-only draft is normalized onto a copy as ``title`` (the caller's
    dict is never mutated). Returns ``(well_formed, dropped_count)``.
    """
    well_formed: list[dict[str, Any]] = []
    for d in raw:
        draft_title = _draft_title(d)
        if draft_title is None:
            continue
        if isinstance(d.get("title"), str):
            well_formed.append(d)
        else:
            # name-only — normalize onto a copy so the relay sees a ``title``.
            well_formed.append({**d, "title": draft_title})
    return well_formed, len(raw) - len(well_formed)


@mcp.tool()
async def propose_batch(drafts: list[dict[str, Any]], title: str = "") -> str:
    """Submit a MegaTask — SEVERAL task drafts at once — for the human to confirm.

    Use this instead of ``propose_draft`` when the CEO asked for multiple tasks
    across the scoped repos. ``drafts`` is a list where each item has the same
    fields as a single draft PLUS a collision surface — ``intends_to_touch[]``
    (files/dirs it will modify), ``adds_migration`` (adds a DB migration?),
    ``touches_shared`` (edits a widely shared component?). The system sequences
    them into conflict-free waves; over-declaring a surface is safer than
    under-declaring. ``title`` names the MegaTask. Each draft targets its repos
    via the per-cell ``project_id`` on its ``the_work[]`` entries (a multi-cell
    task has one project_id per cell; a single-cell task may use one the_work
    entry or a top-level ``project_id``).
    """
    session_id = os.environ.get("ROBOCO_PROMPTER_SESSION_ID", "")
    if not session_id:
        return (
            "No live session id (ROBOCO_PROMPTER_SESSION_ID) — cannot surface the "
            "MegaTask."
        )
    # Malformed entries are dropped and counted; an empty batch is refused
    # rather than silently vanishing on the panel side (#163).
    well_formed, dropped = _normalize_batch_drafts(drafts or [])
    if not well_formed:
        return (
            "That MegaTask had no well-formed task drafts — give each a title"
            " (or name) and a project_id and call propose_batch again."
        )
    payload = {
        "drafts": well_formed,
        "title": title,
        "dropped": dropped,
    }
    result = await post_batch(session_id, payload)
    if result.get("ok"):
        if dropped:
            return (
                "MegaTask submitted — the human can review it in the panel."
                f" {dropped} draft(s) were dropped for missing a title/name."
            )
        return "MegaTask submitted — the human can review it in the panel."
    detail = result.get("detail") or result.get("error") or "unknown error"
    return f"Could not submit the MegaTask to the panel: {detail}"


if __name__ == "__main__":
    mcp.run()
