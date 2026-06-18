// opencode plugin — the Intake interviewer's propose_draft tool, on Grok.
//
// Parity with the Claude Intake's SDK tool (roboco.agent_sdk.intake_driver
// .build_intake_options): the model calls propose_draft once the task spec is
// ready, and the driver (OpencodeServeSession.normalize_opencode_message ->
// _is_propose_draft -> _draft_from_tool_input) turns that tool call into the
// `draft` chunk the panel renders as the reviewable draft card.
//
// Without this, propose_draft is a tool the prompter prompt tells the model to
// call but that does not exist on Grok, so no draft card ever appears and the
// human can't launch a task from a Grok intake chat. The execute() only ACKs —
// the payload that matters is the tool-CALL input, which the driver intercepts.
//
// Loaded ONLY into the roboco-agent-grok-prompter image via
// ROBOCO_OPENCODE_EXTRA_PLUGINS (the one-shot delivery roles never draft).
//
// UNVERIFIED-LIVE: opencode's exact tool-call Part shape in the synchronous
// serve reply — confirm a Grok intake spec yields a draft chunk -> panel card
// on the NAS before relying on Grok intake.

import { tool } from "@opencode-ai/plugin";

// Named export + loaded from the plugin auto-discovery dir
// (~/.config/opencode/plugin/) — opencode 1.17.8 only registers Hooks.tool from
// directory auto-discovery, not a config `plugin:`-array absolute path
// (verified live).
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
      async execute() {
        // The driver intercepts the tool CALL and emits the draft chunk; this
        // handler only acknowledges so the model knows the draft landed.
        return "Draft submitted — the human can review it.";
      },
    }),
  },
});
