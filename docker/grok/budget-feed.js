// opencode plugin — budget / loop / terminal feed for one-shot RoboCo Grok agents.
//
// Ports the Claude PostToolUse budget hook (docker/scripts/post-tool-budget-hook.sh)
// and the Stop hook's terminal-tool tracking (docker/scripts/stop-hook.sh) to
// opencode's tool.execute.{before,after}. The in-container SDK server
// (roboco.agent_sdk.server, :9000) is the same long-lived process the Claude
// path runs — the grok entrypoint starts it before `opencode run`. The flow/do
// MCP servers already POST /verb/attempted to it for the per-verb circuit
// breaker, so starting it + this feed restores the budget/loop/terminal cluster
// on Grok (Claude-parity, the CEO's "create what's missing" call).
//
//   before: read /budget/status and DENY (throw) on a hard halt, or a loop with
//           loop_action=halt. opencode has no PostToolUse-deny, so the pre-exec
//           gate is the only place to stop a runaway one-shot run from burning
//           the cost cap mid-turn. The loop trips one call later than Claude
//           (record is in `after`) but still halts the burn.
//   after:  record the executed tool on /terminal/tool_recorded (so a graceful
//           terminal verb is recognized) and /budget/tool_called (advances the
//           breaker/loop counters and feeds the post-exit post-mortem).
//
// Fail policy: the `after` POSTs never block (recording can't risk spend). The
// `before` gate fails OPEN by default, but fails CLOSED when ROBOCO_BUDGET_ENFORCE=1
// (set by the one-shot entrypoint, which always starts the SDK budget server) and
// the budget server is unreachable — an unenforceable cost cap on a task agent is
// the one case worth halting for. Interactive serve images (intake / secretary)
// own :9000 for the human-turn receiver, run NO SDK budget server, and set no
// ENFORCE flag, so their tool calls always proceed (these POSTs 404 there).

const SDK_URL = process.env.ROBOCO_SDK_URL || "http://localhost:9000";

async function sdk(method, path, body) {
  try {
    const res = await fetch(`${SDK_URL}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(2000),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null; // fail-open — never block the agent on SDK reachability
  }
}

// Canonical, dependency-free serialization (sorted keys) so the SDK's loop
// detector sees identical (tool, args) calls as identical. Need NOT match the
// Claude hook's sha256 — only be stable within one session.
function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  const keys = Object.keys(value).sort();
  return (
    "{" +
    keys.map((k) => JSON.stringify(k) + ":" + canonical(value[k])).join(",") +
    "}"
  );
}

function argsHash(args) {
  const s = canonical(args ?? {});
  let h = 0x811c9dc5; // FNV-1a, 32-bit
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

// opencode namespaces an MCP tool as "<server>_<verb>" (or "<server>.<verb>");
// the Claude path's verbs arrive bare. Strip a known roboco-* server prefix so
// the SDK recognizes a terminal verb (i_am_idle / i_am_done / ...) — the SDK's
// own "__"-split is a no-op on the already-bare verb this returns.
// Verified live: opencode delivers MCP tools as "roboco-flow_<verb>" (underscore);
// the "." form and an mcp__ prefix are still handled defensively.
function bareVerb(tool) {
  const mcp = tool.match(/^mcp__[a-z0-9-]+__(.+)$/);
  if (mcp) return mcp[1];
  const pref = tool.match(/^roboco-[a-z-]+[_.](.+)$/);
  if (pref) return pref[1];
  return tool;
}

// Named export (opencode's plugin convention) + baked into the plugin
// auto-discovery dir (~/.config/opencode/plugin/) at image build — the simplest
// registration route (no config `plugin:` path needed).
export const RobocoBudgetFeed = async () => {
  return {
    "tool.execute.before": async (input) => {
      const status = await sdk("GET", "/budget/status", null);
      if (!status) {
        // One-shot delivery agents MUST have the in-container SDK budget server
        // (the entrypoint starts it and exports ENFORCE=1). A missing signal
        // there means the cost cap is unenforceable — fail CLOSED to stop an
        // uncapped burn. Interactive serve agents set no flag → fail open.
        if (process.env.ROBOCO_BUDGET_ENFORCE === "1") {
          throw new Error(
            "[Halt] budget server unreachable — failing closed to prevent " +
              "uncapped token spend. Stop now with i_am_idle() or unclaim().",
          );
        }
        return; // fail-open (no budget server expected for this role)
      }
      if (status.halt) {
        throw new Error(
          `[Halt] tool budget exhausted (${status.total}/${status.halt_threshold}). ` +
            "Stop now — release the task with unclaim() or i_am_idle().",
        );
      }
      if (status.loop && status.loop_action === "halt") {
        throw new Error(
          "[Loop] same tool+args repeated in window — halting. " +
            "Release the task with unclaim() or stop with i_am_idle().",
        );
      }
    },
    "tool.execute.after": async (input) => {
      const raw = String(input?.tool || "");
      if (!raw) return;
      await sdk("POST", "/terminal/tool_recorded", { tool: bareVerb(raw) });
      await sdk("POST", "/budget/tool_called", {
        tool: bareVerb(raw),
        args_hash: argsHash(input?.args),
      });
    },
  };
};
