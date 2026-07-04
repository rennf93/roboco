#!/usr/bin/env bash
# Test harness for docker/scripts/fable-*.sh (mirrors bash-guard-tests.sh).
#
# Feeds each hook synthetic Claude Code JSON via stdin and asserts the
# expected output shape. Deny/block decisions on these ported hooks are
# signaled via a JSON stdout body (exit 0), not exit code 2 — the same
# structured hook-output contract the upstream scripts use verbatim (see
# docs/superpowers/plans/2026-07-04-v0.18.0-A-opus-fable-plan.md, Task 8
# deviation note: this differs from bash-guard-hook.sh's own exit-2
# convention, but matches the real upstream bytes being ported).
#
# Run:
#   bash docker/scripts/tests/fable-hooks-tests.sh
#
# Exit 0 on full pass, 1 on any failure.

set -u

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASH_DISCIPLINE="$SCRIPTS_DIR/fable-bash-discipline-hook.sh"
HONESTY_NUDGE="$SCRIPTS_DIR/fable-honesty-nudge-hook.sh"
STOP_GATE="$SCRIPTS_DIR/fable-stop-gate-hook.sh"
PROMPT_NUDGE="$SCRIPTS_DIR/fable-prompt-nudge-hook.sh"

PASS=0
FAIL=0
FAILS=()

_record() {
    local label="$1" ok="$2"
    if [[ "$ok" == "1" ]]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILS+=("$label")
    fi
}

# ---------- fable-bash-discipline-hook.sh (PreToolUse[Bash]) ----------
run_bash_discipline() {
    local label="$1" cmd="$2" expect_deny="$3"
    local json out
    json=$(python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]},"session_id":"s1"}))' "$cmd")
    out=$(printf '%s' "$json" | bash "$BASH_DISCIPLINE" 2>/dev/null)
    if [[ "$expect_deny" == "1" ]]; then
        if printf '%s' "$out" | grep -q '"permissionDecision": "deny"'; then
            _record "$label" 1
        else
            _record "$label (expected deny, got: $out)" 0
        fi
    else
        if [[ -z "$out" ]]; then
            _record "$label" 1
        else
            _record "$label (expected silent allow, got: $out)" 0
        fi
    fi
}

run_bash_discipline "deny bare cat"       "cat foo.py"           1
run_bash_discipline "deny bare head"      "head -20 foo.py"      1
run_bash_discipline "deny sed -n"         "sed -n '1,5p' foo.py" 1
run_bash_discipline "allow cat pipe grep" "cat foo.py | grep x"  0
run_bash_discipline "allow ls"            "ls -la"               0
run_bash_discipline "allow git status"    "git status"           0

# ---------- fable-honesty-nudge-hook.sh (PostToolUse[Bash]) ----------
run_honesty_nudge() {
    local label="$1" resp="$2" expect_hit="$3"
    local json out
    json=$(python3 -c 'import json,sys; print(json.dumps({"tool_response":sys.argv[1],"session_id":"s1"}))' "$resp")
    out=$(printf '%s' "$json" | bash "$HONESTY_NUDGE" 2>/dev/null)
    if [[ "$expect_hit" == "1" ]]; then
        if printf '%s' "$out" | grep -q '"additionalContext"'; then
            _record "$label" 1
        else
            _record "$label (expected additionalContext, got: $out)" 0
        fi
    else
        if [[ -z "$out" ]]; then
            _record "$label" 1
        else
            _record "$label (expected silent, got: $out)" 0
        fi
    fi
}

run_honesty_nudge "nudge on AssertionError" 'Traceback (most recent call last): AssertionError: boom' 1
run_honesty_nudge "nudge on pytest FAILURES" '===== FAILURES =====' 1
run_honesty_nudge "silent on clean output" 'all good, 12 passed' 0

# ---------- fable-stop-gate-hook.sh (Stop/SubagentStop) ----------
_mk_transcript() {
    local text="$1" path="$2"
    python3 -c '
import json, sys
text, path = sys.argv[1], sys.argv[2]
with open(path, "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
' "$text" "$path"
}

run_stop_gate() {
    local label="$1" text="$2" mode="$3" expect_block="$4"
    local tmp json out
    tmp=$(mktemp)
    _mk_transcript "$text" "$tmp"
    json=$(python3 -c 'import json,sys; print(json.dumps({"transcript_path":sys.argv[1],"session_id":"s1","stop_hook_active":False}))' "$tmp")
    if [[ -n "$mode" ]]; then
        out=$(printf '%s' "$json" | bash "$STOP_GATE" "$mode" 2>/dev/null)
    else
        out=$(printf '%s' "$json" | bash "$STOP_GATE" 2>/dev/null)
    fi
    rm -f "$tmp"
    if [[ "$expect_block" == "1" ]]; then
        if printf '%s' "$out" | grep -q '"decision": "block"'; then
            _record "$label" 1
        else
            _record "$label (expected block, got: $out)" 0
        fi
    else
        if [[ -z "$out" ]]; then
            _record "$label" 1
        else
            _record "$label (expected silent, got: $out)" 0
        fi
    fi
}

run_stop_gate "main: blocks on deferral"      "I'll fix this next."                        ""         1
run_stop_gate "main: silent on verified done" "Fixed and verified: all 12 tests pass."     ""         0
run_stop_gate "subagent: blocks on offer"     "Would you like me to also check the docs?"  "subagent" 1
run_stop_gate "subagent: silent on findings"  "Found 3 issues: A, B, C, all reproduced."    "subagent" 0

# ---------- fable-prompt-nudge-hook.sh (UserPromptSubmit) ----------
run_prompt_nudge() {
    local label="$1" prompt="$2" expect_substr="$3"
    local json out
    json=$(python3 -c 'import json,sys; print(json.dumps({"prompt":sys.argv[1]}))' "$prompt")
    out=$(printf '%s' "$json" | bash "$PROMPT_NUDGE" 2>/dev/null)
    if printf '%s' "$out" | grep -qF "$expect_substr"; then
        _record "$label" 1
    else
        _record "$label (got: $out)" 0
    fi
}

run_prompt_nudge "question -> assess-only" "Why is the deploy failing?" "question-shaped"
run_prompt_nudge "imperative -> reminders" "Fix the deploy pipeline."   "Fable reminders"

# ---------- Report ----------
echo
echo "===== fable-hooks tests ====="
echo "  passed: $PASS"
echo "  failed: $FAIL"
if (( FAIL > 0 )); then
    echo "  failures:"
    for f in "${FAILS[@]}"; do
        echo "    - $f"
    done
    exit 1
fi
exit 0
