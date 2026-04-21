#!/usr/bin/env bash
# Stop hook — prevent silent exits without a terminal transition.
#
# An agent should never "just stop" mid-task. They must call one of the
# terminal MCP tools first (roboco_agent_idle, roboco_task_substitute,
# roboco_task_escalate, roboco_task_pause, roboco_task_block, submit_qa,
# qa_pass/fail, docs_complete, task_complete, task_cancel). Otherwise the
# task stays in `claimed` / `in_progress` forever and the PM has to hand-
# unstick it.
#
# This hook blocks the Stop on the first ungraceful attempt (exit 2 with a
# reminder). If the agent tries to Stop again anyway, SDK state shows
# stop_attempts > stop_allowance — we let the Stop through AND fire-and-
# forget the auto-substitute so the task at least gets released.
#
# SDK state comes from /terminal/stop_attempt, which BOTH increments the
# counter AND returns the current terminal/last-tool status.

set -u

SDK_URL="${ROBOCO_SDK_URL:-http://localhost:9000}"

resp=$(curl -sf -m 2 -X POST "$SDK_URL/terminal/stop_attempt" 2>/dev/null)

# SDK unreachable — fail OPEN (don't block shutdown indefinitely).
if [[ -z "$resp" ]]; then
    exit 0
fi

had_terminal=$(echo "$resp" | jq -r '.had_terminal_recently // false')
attempts=$(echo "$resp" | jq -r '.stop_attempts // 0')
allowance=$(echo "$resp" | jq -r '.stop_allowance // 1')
last_tool=$(echo "$resp" | jq -r '.last_tool // "null"')

# Graceful: a terminal tool was called in the recent-tool window.
if [[ "$had_terminal" == "true" ]]; then
    exit 0
fi

# Beyond allowance — let the Stop through to avoid hanging the container,
# but auto-substitute the task so the PM doesn't have to clean up.
if (( attempts > allowance )); then
    curl -sf -m 2 -X POST "$SDK_URL/terminal/force_substitute" >/dev/null 2>&1 || true
    echo "[Stop] Auto-substituted task after ${attempts} ungraceful stop attempts (last tool: ${last_tool})."
    exit 0
fi

# First ungraceful attempt: nudge the agent to call a terminal tool.
cat >&2 <<EOF
Denied: you stopped without calling a terminal tool. The task is still
assigned to you and will not be handed off.

Call one of:
  - roboco_agent_idle()                       # no work remains
  - roboco_task_substitute(reason="...")      # release the task
  - roboco_task_escalate(reason="...")        # escalate to PM
  - roboco_task_pause(checkpoint="...")       # save progress, come back
  - roboco_task_submit_qa() / qa_pass() / qa_fail() / docs_complete() / task_complete()

Then stop again. If you genuinely cannot transition, a second stop will
auto-substitute with reason="stopped_without_transition" (recorded).
EOF
exit 2
