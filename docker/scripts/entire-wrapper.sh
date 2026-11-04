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

if [ "$1" != "hooks" ]; then
    exec "$REAL_ENTIRE" "$@"
fi

# Entire writes its git objects 0600. The git user shares only the agent
# group, so an object it cannot read makes every fetch call the clone corrupt
# (live: fe-dev-1's sync_branch, 2026-11-03). Grant that group what this uid
# left under objects/ and refs/entire/, as RoboCo's post-commit does.
"$REAL_ENTIRE" "$@"
status=$?
common=$(git -c safe.directory='*' rev-parse --path-format=absolute --git-common-dir 2>/dev/null) &&
    find "$common/objects" "$common/refs/entire" -user "$(id -u)" \( \
        \( -type d ! -perm -g=rwx -exec chmod g+rwx {} + \) -o \
        \( -type f ! -perm -g=r -exec chmod g+r {} + \) \) 2>/dev/null
exit "$status"
