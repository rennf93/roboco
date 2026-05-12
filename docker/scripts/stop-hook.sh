#!/usr/bin/env bash
# Stop hook — prevent silent exits without a terminal transition.
#
# An agent should never "just stop" mid-task. They must call a terminal
# gateway verb first (i_am_idle, i_am_done, i_am_blocked, pass, fail,
# i_documented, complete, escalate_up, unclaim) so the task is not left
# stuck in `claimed` / `in_progress` for the PM to hand-unstick.
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
{
  echo "Denied: you stopped without calling a terminal tool. The task is"
  echo "still assigned to you and will not be handed off. Call one of:"
  case "${ROBOCO_AGENT_ROLE:-}" in
    developer|documenter)
      echo "  - i_am_done(task_id, notes)   # work submitted for QA"
      echo "  - i_am_blocked(reason)        # stuck, need PM"
      echo "  - i_am_idle()                 # no work remains"
      ;;
    qa)
      echo "  - pass(task_id, notes) / fail(task_id, issues)"
      echo "  - i_am_idle()"
      ;;
    cell_pm|main_pm)
      echo "  - complete(task_id, notes) / escalate_up(task_id, notes)"
      echo "  - i_am_idle()"
      ;;
    *)
      echo "  - i_am_idle()  # default terminal verb for any role"
      ;;
  esac
  echo "Then stop again. A second ungraceful stop auto-releases the task (recorded)."
} >&2
exit 2
