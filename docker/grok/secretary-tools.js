// opencode plugin — the Secretary's CEO-authority tools, on Grok.
//
// Parity with the Claude Secretary's SDK tools (roboco.agent_sdk.secretary_driver
// .build_secretary_options): read_company_state / read_task / submit_directive,
// each calling the backend /api/secretary/* routes with the container's HMAC
// agent token. Without these the Grok Secretary can chat but cannot read company
// state or act on a CEO directive — the integration blocker.
//
// Loaded ONLY into the roboco-agent-grok-secretary image via
// ROBOCO_OPENCODE_EXTRA_PLUGINS (so no other role gets CEO authority). The
// container already carries ROBOCO_AGENT_TOKEN / ROBOCO_API_URL / ROBOCO_AGENT_ID
// / ROBOCO_AGENT_ROLE (set by the orchestrator's _build_secretary_run_cmd), so
// the auth substrate matches the one-shot Grok path exactly.
//
// The backend gate-list queues high-impact directive kinds (charter,
// control_task, approve_pitch, announce) for the CEO's explicit confirmation and
// runs relay_message directly — that policy lives server-side; this plugin only
// forwards the call. Each tool returns the backend JSON as a string the model
// reads back (mirrors secretary_driver._text_result).
//
// UNVERIFIED-LIVE: the @opencode-ai/plugin tool-registration path against a live
// opencode serve + grok-build-0.1 — confirm a submit_directive round-trips with
// the HMAC token on the NAS before routing real CEO directives through Grok.

import { tool } from "@opencode-ai/plugin";

const API_BASE = (
  process.env.ROBOCO_API_URL || "http://roboco-orchestrator:8000"
).replace(/\/+$/, "");
const TIMEOUT_MS = 30000;

function headers() {
  const h = {
    "Content-Type": "application/json",
    "X-Agent-ID": process.env.ROBOCO_AGENT_ID || "",
    "X-Agent-Role": process.env.ROBOCO_AGENT_ROLE || "secretary",
  };
  const token = process.env.ROBOCO_AGENT_TOKEN;
  if (token) h["X-Agent-Token"] = token;
  return h;
}

// Call /api/secretary{path}; never throw — a failure becomes an {error,...}
// object the model can read and report, exactly like secretary_driver._call_backend.
async function callBackend(method, path, body) {
  let res;
  try {
    res = await fetch(`${API_BASE}/api/secretary${path}`, {
      method,
      headers: headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (e) {
    return { error: "request_failed", detail: String(e) };
  }
  let data;
  try {
    data = await res.json();
  } catch {
    data = { detail: await res.text().catch(() => "") };
  }
  if (!res.ok) return { error: `http_${res.status}`, detail: data };
  return data;
}

const asText = (data) => JSON.stringify(data);

// Named export (opencode's plugin convention) + baked into the plugin
// auto-discovery dir (~/.config/opencode/plugin/) at image build. Verified live
// against grok-build-0.1: the model called read_company_state + submit_directive
// and the backend received both requests with the X-Agent-Token.
export const RobocoSecretaryTools = async () => ({
  tool: {
    read_company_state: tool({
      description:
        "Read a compact snapshot of company state: the charter (goals), task " +
        "counts by status, pending pitches, and any directives awaiting the " +
        "CEO's confirmation.",
      args: {},
      async execute() {
        return asText(await callBackend("GET", "/state"));
      },
    }),
    read_task: tool({
      description: "Read one task's detail by its id.",
      args: { task_id: tool.schema.string().describe("The task id") },
      async execute(args) {
        const id = encodeURIComponent(String(args.task_id));
        return asText(await callBackend("GET", `/tasks/${id}`));
      },
    }),
    submit_directive: tool({
      description:
        "Act on the CEO's command. 'kind' is one of: relay_message " +
        "(payload: channel, text), update_charter (payload: charter), " +
        "control_task (payload: task_id, action[start|cancel|override], " +
        "status?), approve_pitch (payload: pitch_id, notes?), announce " +
        "(payload: text). High-impact kinds (charter, control_task, " +
        "approve_pitch, announce) are queued for the CEO's explicit " +
        "confirmation; relay_message runs directly.",
      args: {
        kind: tool.schema.string().describe("The directive kind"),
        payload: tool.schema
          .record(tool.schema.string(), tool.schema.any())
          .describe("The directive payload object"),
      },
      async execute(args) {
        return asText(
          await callBackend("POST", "/directives", {
            kind: args.kind,
            payload: args.payload || {},
          }),
        );
      },
    }),
  },
});
