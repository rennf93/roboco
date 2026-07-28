#!/usr/bin/env bash
# Kimi's [[hooks]] TOML entry has no `env` field — an entry carrying one is
# silently dropped whole (live-verified: "Ignored invalid config ... hooks",
# run continues with NO hooks installed at all). ROBOCO_GUARD_SKIP_GIT=1 must
# therefore ride a wrapper's own export, not the hook config, since kimi's
# `command` is a plain path (unverified whether it shell-interprets the
# string, so `env VAR=1 cmd` is not assumed safe).
export ROBOCO_GUARD_SKIP_GIT=1
exec /app/scripts/bash-guard-hook.sh "$@"
