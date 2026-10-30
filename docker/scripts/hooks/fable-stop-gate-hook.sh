#!/usr/bin/env bash
# Fable turn-discipline gate: block a Stop/SubagentStop whose final paragraph
# promises or defers work instead of doing it. Ported from opus-fable-playbook
# hooks/stop-gate.sh (v0.1.3) — see docs/superpowers/plans/2026-07-04-v0.18.0-A-opus-fable-plan.md.
# Usage: fable-stop-gate-hook.sh [subagent]. Fail-open: any internal error => exit 0.
set -u

INPUT="$(cat)" || exit 0
py() { printf '%s' "$INPUT" | python3 -c "$1" 2>/dev/null || true; }

ACTIVE="$(py 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active", False))')"
[ "$ACTIVE" = "True" ] && exit 0

last_message_py() { cat <<'PY'
import json, sys
try:
    hook = json.load(sys.stdin)
    last = ""
    with open(hook.get("transcript_path", ""), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant" or obj.get("isSidechain"):
                continue
            content = (obj.get("message") or {}).get("content") or []
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if any(t.strip() for t in texts):
                last = "\n".join(t for t in texts if t)
    sys.stdout.write(last)
except Exception:
    pass
PY
}
# NOTE (deviation from the plan's literal Task 5 Step 1 text): the plan's own
# script piped $INPUT into `python3 - <<'PY' ... PY`, but `python3 -` and the
# heredoc both claim stdin — the heredoc always wins, so the piped JSON never
# reaches json.load(sys.stdin) and this extraction silently returns empty
# every time (verified: the hook then never blocks anything). The same
# pipe-into-`python3 -<<PY` idiom is also present in three existing hooks
# (user-prompt-hook.sh, post-tool-budget-hook.sh, usage-report-hook.sh) —
# out of scope to fix here, flagged separately. Fix: pass the script via
# `-c "$(cat <<'PY' ... PY)"` so python3's stdin is left free for the pipe.
LAST="$(printf '%s' "$INPUT" | python3 -c "$(last_message_py)" 2>/dev/null)" || exit 0
[ -z "$LAST" ] && exit 0

FINAL="$(printf '%s' "$LAST" | awk -v RS='' 'END{print}')"
[ -z "$FINAL" ] && exit 0

VERBS='(start|begin|proceed|continue|create|implement|write|update|fix|add|run|check|investigate|work|make|set|move|look|open|draft|explore|apply|push|refactor|clean|test)'
MATCH=""
printf '%s' "$FINAL" | grep -qiE "(^|[^a-z])i('|'?)?ll (now |then |next |also |go ahead and )?$VERBS" && MATCH=1
[ -z "$MATCH" ] && printf '%s' "$FINAL" | grep -qiE "(^|[^a-z])i will (now |then |next |also )?$VERBS" && MATCH=1
[ -z "$MATCH" ] && printf '%s' "$FINAL" | grep -qiE "(^|[[:space:]])next steps?:" && MATCH=1
[ -z "$MATCH" ] && printf '%s' "$FINAL" | grep -qiE "let me know (if|when|whether|what|which|and)" && MATCH=1
[ -z "$MATCH" ] && printf '%s' "$FINAL" | grep -qiE "would you like me to" && MATCH=1
[ -z "$MATCH" ] && printf '%s' "$FINAL" | grep -qiE "(^|[^a-z])shall i " && MATCH=1
[ -z "$MATCH" ] && printf '%s' "$FINAL" | grep -qiE "(^|[^a-z])want me to (continue|proceed|keep going|finish|do the rest)" && MATCH=1

[ -z "$MATCH" ] && exit 0

MODE="${1:-main}"
if [ "$MODE" = "subagent" ]; then
  REASON="Fable subagent discipline: your final message is your return value. Return your findings now — conclusions with evidence, not intentions, plans, or offers."
else
  REASON="Fable turn discipline: your last paragraph promises or proposes work instead of doing it. Do that work now — retry errors and gather missing information yourself. If you are genuinely blocked on something only the user can provide, state that blocking question plainly and stop."
fi
printf '{"decision": "block", "reason": "%s"}' "$REASON"
exit 0
