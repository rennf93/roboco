#!/bin/sh
# Installed as /usr/local/bin/entire in the agent images; the pinned Entire
# CLI itself lives at /usr/local/lib/entire/entire.
#
# The roboco repo commits Entire's Claude Code hooks (.claude/settings.json),
# so every agent working on it calls `entire` on each session event and edit.
# ROBOCO_ENTIRE_ENABLED is set per container at spawn from the
# `entire_enabled` feature flag:
#   off -> every hook call is a silent no-op; any other command explains why
#          Entire is unavailable instead of running.
#   on  -> session hooks run the real CLI. Brain (`entire brain ...`) is not
#          provisioned in the fleet yet, so its edit hook stays a no-op and its
#          commands say so.
REAL_ENTIRE=/usr/local/lib/entire/entire

is_hook_call() {
    [ "$1" = "hooks" ] || { [ "$1" = "brain" ] && [ "$2" = "hook" ]; }
}

if [ "${ROBOCO_ENTIRE_ENABLED:-false}" != "true" ]; then
    is_hook_call "$@" && exit 0
    echo "Entire is turned off in this RoboCo deployment (feature flag entire_enabled)." >&2
    exit 1
fi

if [ "$1" = "brain" ]; then
    is_hook_call "$@" && exit 0
    echo "Entire Brain is not available to RoboCo agents yet; use the task context instead." >&2
    exit 1
fi

exec "$REAL_ENTIRE" "$@"
