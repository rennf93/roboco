#!/usr/bin/env bash
# Fable PreCompact guidance: shapes what survives conversation compaction.
# Ported from opus-fable-playbook hooks/precompact.sh (v0.1.3) — see
# docs/superpowers/plans/2026-07-04-v0.18.0-A-opus-fable-plan.md.
# Static output, no input parsing needed. Fail-open.
set -u
cat > /dev/null || true
cat <<'EOF'
Compaction guidance (fable-mode): the summary must preserve, outcome-first:
(1) current task state and remaining work, (2) what was verified, with the
actual results, (3) any failures not yet reported to the user, verbatim,
(4) pending user decisions, (5) paths of files being modified.
EOF
exit 0
