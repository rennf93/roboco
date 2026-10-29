#!/bin/sh
# Entrypoint prefix for agent images whose real entrypoint is a binary
# (`claude`, `python -m ...`) rather than a script that sets it itself.
#
# Group-writable output: the orchestrator's workspace git user shares the
# agent group and must be able to rewrite what this agent creates (checkout,
# reset or rebase over agent-made files and directories).
umask 002
exec "$@"
