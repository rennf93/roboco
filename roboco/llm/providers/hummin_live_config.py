"""Render the hummin (pi) extension that bridges live-chat tools via fetch.

hummin has NO MCP client (a verified upstream fact - see
:mod:`roboco.llm.providers.hummin`), but it is a pi fork with the extension
API: TypeScript modules under ``$HUMMIN_CODING_AGENT_DIR/extensions/`` auto-
load and can register custom tools via ``pi.registerTool()``. This module
renders one extension that gives the live Intake/Secretary chat the SAME
backend tool surface the MCP-capable CLIs get through their rendered
mcpServers:

  * secretary: read_company_state / read_task / search_tasks /
    submit_directive  (mirrors :mod:`roboco.mcp.secretary_server` and
    ``secretary_driver._call_backend`` byte-for-byte on the wire: same
    ``/api/secretary/*`` paths, same X-Agent-* auth headers)
  * intake: propose_draft / propose_batch / search_past_tasks  (mirrors
    :mod:`roboco.mcp.intake_server`: draft/batch POST to the
    ``/api/prompter/live/{session}/events`` relay, search hits the
    bounded ``search-tasks`` endpoint)

Auth is the container's HMAC env (ROBOCO_AGENT_ID / ROLE / TOKEN) read at
tool-call time via ``process.env`` - the same substrate the one-shot hummin
path and the MCP servers use. Network calls use the global ``fetch``
(Node 18+ inside hummin's runtime).
"""

from __future__ import annotations

import os
from pathlib import Path

_HUMMIN_AGENT_DIR = Path(
    os.environ.get("HUMMIN_CODING_AGENT_DIR", "/home/agent/.hummin/agent")
)
_EXTENSIONS_SUBDIR = "extensions"
_EXTENSION_NAME = "roboco-live-tools.ts"

_SECRETARY_TOOLS_TS = r"""
  pi.registerTool({
    name: "read_company_state",
    label: "Read company state",
    description: "Read a compact snapshot of company state: the charter (goals), task counts by status, pending pitches, and any directives awaiting the CEO's confirmation.",
    parameters: Type.Object({}),
    async execute() {
      return textResult(JSON.stringify(await api("GET", "/state")));
    },
  });

  pi.registerTool({
    name: "read_task",
    label: "Read task",
    description: "Read one task's full detail by its id.",
    parameters: Type.Object({ task_id: Type.String({ description: "Task id" }) }),
    async execute(_id, params) {
      return textResult(JSON.stringify(await api("GET", "/tasks/" + encodeURIComponent(params.task_id))));
    },
  });

  pi.registerTool({
    name: "search_tasks",
    label: "Search tasks",
    description: "Resolve a task NAME to concrete task ids (title/description substring, min 2 chars).",
    parameters: Type.Object({
      q: Type.String({ description: "Title/description substring" }),
      limit: Type.Optional(Type.Number({ description: "Max matches (default 20)" })),
    }),
    async execute(_id, params) {
      const limit = Number(params.limit) > 0 ? Number(params.limit) : 20;
      const result = await api("GET", "/tasks?q=" + encodeURIComponent(params.q) + "&limit=" + limit);
      return textResult(JSON.stringify(Array.isArray(result) ? { tasks: result } : result));
    },
  });

  pi.registerTool({
    name: "submit_directive",
    label: "Submit directive",
    description: "Act on the CEO's command. kind: relay_message | update_charter | control_task | approve_pitch | announce. High-impact kinds are queued for the CEO's confirmation.",
    parameters: Type.Object({
      kind: Type.String({ description: "Directive kind" }),
      payload: Type.Optional(Type.Object({}, { additionalProperties: true })),
    }),
    async execute(_id, params) {
      return textResult(JSON.stringify(await api("POST", "/directives", { kind: params.kind, payload: params.payload || {} })));
    },
  });
"""

_INTAKE_TOOLS_TS = r"""
  const draftSchema = Type.Object({
    title: Type.String({ description: "Short imperative task title" }),
    objective: Type.String({
      description:
        "What this task does and why, 20+ characters. The panel shows it as the summary and REFUSES to launch without it.",
    }),
    what_this_builds: Type.Array(Type.String(), {
      description: "User-visible outcomes, one string each",
    }),
    the_work: Type.Array(
      Type.Object({
        team: Type.String({ description: "Delivery cell: backend | frontend | uxui" }),
        summary: Type.String({ description: "What this cell builds" }),
        items: Type.Array(Type.String(), { description: "Work items for this cell" }),
        project_id: Type.String({ description: "This cell's repo/project id (per-cell repo). __SCOPED_PROJECT__" }),
      }),
      { description: "One entry per delivery cell; a multi-cell task has one entry per cell" },
    ),
    notes: Type.Optional(Type.Array(Type.String(), { description: "Context notes for the cells" })),
    acceptance_criteria: Type.Array(Type.String(), {
      description: "At least one checkable condition; the panel REFUSES to launch without one",
    }),
    team: Type.Optional(Type.String({ description: "Owning team, if single-cell" })),
    scale: Type.Optional(Type.String({ description: "single | multi (multi = multi-cell)" })),
    task_type: Type.Optional(Type.String()),
    nature: Type.Optional(Type.String()),
    estimated_complexity: Type.Optional(Type.String({ description: "low | medium | high" })),
    priority: Type.Optional(Type.Number({ description: "1 highest, default 2" })),
  });

  pi.registerTool({
    name: "propose_draft",
    label: "Propose task draft",
    description:
      "Submit the finished task draft for the human to review and confirm. Call ONCE when the spec is " +
      "settled. The draft object's field names are EXACT snake_case: title, objective (20+ chars), " +
      "what_this_builds[], the_work[] ({team, summary, items, project_id}, one entry per delivery cell), " +
      "notes[], acceptance_criteria[] (at least one), team, scale, task_type, nature, estimated_complexity, " +
      "priority. Do not invent other field names; unspecified keys are ignored by the panel.",
    parameters: Type.Object({ draft: draftSchema }),
    async execute(_id, params) {
      return textResult(JSON.stringify(await relayEvent({ kind: "draft", text: "", tool: "propose_draft", data: params.draft })));
    },
  });

  pi.registerTool({
    name: "propose_batch",
    label: "Propose MegaTask batch",
    description:
      "Submit a MegaTask (SEVERAL task drafts at once) to the CEO's live chat for review. Use this instead of " +
      "propose_draft when the CEO asked for multiple tasks. Each draft uses the exact same snake_case fields " +
      "as propose_draft's draft object.",
    parameters: Type.Object({
      title: Type.String({ description: "Batch title" }),
      drafts: Type.Array(draftSchema, { description: "One draft per task, exact same fields as propose_draft" }),
    }),
    async execute(_id, params) {
      return textResult(JSON.stringify(await relayEvent({ kind: "batch", text: "", tool: "propose_batch", data: { drafts: params.drafts, title: params.title } })));
    },
  });

  pi.registerTool({
    name: "search_past_tasks",
    label: "Search past tasks",
    description: "Search completed/past tasks for context relevant to the new task being drafted.",
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      limit: Type.Optional(Type.Number({ description: "Max results (default 8)" })),
    }),
    async execute(_id, params) {
      const limit = Number(params.limit) > 0 ? Number(params.limit) : 8;
      const qs = "q=" + encodeURIComponent(params.query) + "&limit=" + limit;
      let result;
      try {
        const resp = await fetch(API + "/api/prompter/live/" + SESSION + "/search-tasks?" + qs);
        if (!resp.ok) result = { error: "http_" + resp.status, results: [] };
        else result = { results: await resp.json() };
      } catch (exc) {
        result = { error: "request_failed", detail: String(exc), results: [] };
      }
      return textResult(JSON.stringify(result));
    },
  });
"""

_EXTENSION_TS = r"""// AUTO-GENERATED by roboco.llm.providers.hummin_live_config - do not edit.
// The live-chat tool bridge for hummin (pi): the same /api calls the
// MCP-capable CLIs get through their rendered mcpServers.
import { Type } from "typebox";

const API = (process.env.ROBOCO_API_URL || "http://roboco-orchestrator:8000").replace(/\/$/, "");
const SECRETARY_BASE = API + "/api/secretary";
const SESSION = process.env.ROBOCO_SECRETARY_SESSION_ID || process.env.ROBOCO_PROMPTER_SESSION_ID || "";

function authHeaders() {
  const headers = {
    "X-Agent-ID": process.env.ROBOCO_AGENT_ID || "",
    "X-Agent-Role": process.env.ROBOCO_AGENT_ROLE || "secretary",
  };
  const token = process.env.ROBOCO_AGENT_TOKEN;
  if (token && token !== "UNSIGNED") headers["X-Agent-Token"] = token;
  return headers;
}

async function api(method, path, body) {
  try {
    const resp = await fetch(SECRETARY_BASE + path, {
      method,
      headers: { ...authHeaders(), ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!resp.ok) return { error: "http_" + resp.status, detail: (await resp.text()).slice(0, 300) };
    return await resp.json();
  } catch (exc) {
    return { error: "request_failed", detail: String(exc) };
  }
}

async function relayEvent(payload) {
  try {
    const resp = await fetch(API + "/api/prompter/live/" + SESSION + "/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) return { error: "http_" + resp.status, detail: (await resp.text()).slice(0, 300) };
    return { ok: true };
  } catch (exc) {
    return { error: "request_failed", detail: String(exc) };
  }
}

function textResult(text) {
  return { content: [{ type: "text", text }], details: {} };
}

export default function robocoLiveTools(pi) {
__TOOLS__
}
"""


def render_extension(role: str) -> str:
    """The extension TS for one role ('secretary' or 'intake')."""
    tools = _SECRETARY_TOOLS_TS if role == "secretary" else _INTAKE_TOOLS_TS
    # The panel validates the_work[].project_id as a project UUID, which the
    # agent cannot discover from its workspace or prompt. For a single-project
    # scope the orchestrator injects the scoped UUID at spawn; for product and
    # MegaTask scopes the sanctioned path is to OMIT project_id per cell - the
    # CEO binds each repo in the confirm picker.
    scoped_uuid = os.environ.get("ROBOCO_INTAKE_PROJECT_ID", "")
    if scoped_uuid:
        scoped = (
            f"This session's scoped project UUID is '{scoped_uuid}' - use exactly "
            "that value as project_id for the scoped cell (slugs are rejected)."
        )
    else:
        scoped = (
            "No scoped project UUID was injected. Do NOT put a slug in project_id - "
            "the panel accepts only project UUIDs here and rejects anything else. "
            "Omit project_id: the draft stays target-less at proposal time and the "
            "CEO binds the repo in the confirm picker."
        )
    return _EXTENSION_TS.replace("__TOOLS__", tools).replace(
        "__SCOPED_PROJECT__", scoped
    )


def render_extension_file(role: str, agent_dir: Path | None = None) -> Path:
    """Write the extension into the hummin agent dir; return its path.

    Uses the role from ROBOCO_AGENT_ROLE when not given (the live container's
    env), defaulting to intake.
    """
    resolved_role = role or os.environ.get("ROBOCO_AGENT_ROLE", "intake")
    directory = (agent_dir or _HUMMIN_AGENT_DIR) / _EXTENSIONS_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _EXTENSION_NAME
    path.write_text(render_extension(resolved_role), encoding="utf-8")
    return path


if __name__ == "__main__":
    # The hummin live entrypoint runs this to render the extension before
    # exec'ing the generic live driver.
    print(render_extension_file(os.environ.get("ROBOCO_AGENT_ROLE", "intake")))
