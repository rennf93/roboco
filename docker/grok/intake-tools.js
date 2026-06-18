// opencode plugin — the Intake interviewer's propose_draft tool, on Grok.
//
// The model calls propose_draft once the task spec is ready; this delivers the
// draft to the panel's reviewable draft card.
//
// WHY IT POSTS DIRECTLY (not via the driver): opencode's synchronous serve reply
// (POST /session/:id/message) returns only the final assistant text + step
// markers — NOT the tool-CALL parts. So OpencodeServeSession cannot intercept
// this call to emit a `draft` chunk (verified live: a propose_draft call comes
// back as parts=[step-start, text, step-finish], no tool part). Instead the tool
// POSTs the draft straight to the prompter-live relay — the same
// /api/prompter/live/{session}/events endpoint the driver's relay sink uses — so
// the panel renders the card regardless. (The Claude intake path differs: the
// Claude SDK DOES expose the tool-use block, so its driver intercepts it.)
//
// Loaded from the plugin auto-discovery dir (~/.config/opencode/plugin/), baked
// into the grok-prompter image only (the one-shot delivery roles never draft).
// The container provides ROBOCO_API_URL + ROBOCO_PROMPTER_SESSION_ID.

import { tool } from "@opencode-ai/plugin";

const API_BASE = (
  process.env.ROBOCO_API_URL || "http://roboco-orchestrator:8000"
).replace(/\/+$/, "");

export const RobocoIntakeTools = async () => ({
  tool: {
    propose_draft: tool({
      description:
        "Submit the finished task draft for the human to review and confirm. " +
        "Call this once the spec is complete. Pass a JSON object: title, " +
        "objective, what_this_builds[], the_work[] ({team, summary, items}), " +
        "notes[], acceptance_criteria[], team, scale, task_type, nature, " +
        "estimated_complexity, priority.",
      args: {
        draft: tool.schema
          .record(tool.schema.string(), tool.schema.any())
          .describe("The task draft object"),
      },
      async execute(args) {
        const session = process.env.ROBOCO_PROMPTER_SESSION_ID || "";
        if (!session) {
          return "No live session id (ROBOCO_PROMPTER_SESSION_ID) — cannot surface the draft.";
        }
        try {
          const res = await fetch(
            `${API_BASE}/api/prompter/live/${encodeURIComponent(session)}/events`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                kind: "draft",
                text: "",
                tool: "propose_draft",
                data: args.draft || {},
              }),
              signal: AbortSignal.timeout(15000),
            },
          );
          if (!res.ok) {
            return `Draft relay returned HTTP ${res.status}; the human may not see the card.`;
          }
        } catch (e) {
          return "Could not submit the draft to the panel: " + String(e);
        }
        return "Draft submitted — the human can review it in the panel.";
      },
    }),
  },
});
