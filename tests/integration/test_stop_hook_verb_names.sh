#!/usr/bin/env bash
# Smoke: stop-hook + bash-guard-hook list current gateway verbs only.
set -e

cd "$(dirname "$0")/../.."

HOOKS=(docker/scripts/stop-hook.sh docker/scripts/bash-guard-hook.sh)

OLD_VERBS=(
  roboco_agent_idle
  roboco_task_substitute
  roboco_task_pause
  roboco_task_submit_qa
  roboco_task_escalate
  qa_pass
  qa_fail
  docs_complete
  task_complete
)

for hook in "${HOOKS[@]}"; do
  for old in "${OLD_VERBS[@]}"; do
    if grep -q "$old" "$hook"; then
      echo "FAIL: $hook still references pre-gateway verb: $old"
      exit 1
    fi
  done
done

# stop-hook must mention at least one current terminal verb
grep -qE "i_am_idle|unclaim|i_am_blocked|i_am_done|complete|escalate_up" docker/scripts/stop-hook.sh || {
  echo "FAIL: stop-hook.sh lists no current gateway terminal verb"
  exit 1
}

echo "PASS"
