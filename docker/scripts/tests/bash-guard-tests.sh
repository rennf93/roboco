#!/usr/bin/env bash
# Test harness for docker/scripts/bash-guard-hook.sh.
#
# Feeds each command to the hook (via the same JSON stdin contract Claude
# Code uses) and asserts the expected exit code.
#
# Run:
#   bash docker/scripts/tests/bash-guard-tests.sh
#
# Exit 0 on full pass, 1 on any failure.

set -u

HOOK="$(cd "$(dirname "$0")/.." && pwd)/bash-guard-hook.sh"
if [[ ! -x "$HOOK" ]]; then
    # chmod may not have been applied in the dev checkout — run via bash.
    HOOK="bash $HOOK"
fi

PASS=0
FAIL=0
FAILS=()

# run_case <label> <expected_exit> <command>
run_case() {
    local label="$1"
    local expected="$2"
    local cmd="$3"
    local json
    # shellcheck disable=SC2016
    json=$(python3 -c 'import json, sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$cmd")
    local actual
    echo "$json" | $HOOK >/dev/null 2>&1
    actual=$?
    if [[ "$actual" == "$expected" ]]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILS+=("[$label] expected $expected, got $actual | cmd: $cmd")
    fi
}

# ---------- DENY cases (exit 2) ----------
# Git network/auth ops already covered by original hook.
run_case "deny git fetch"           2 "git fetch origin"
run_case "deny compound git push"   2 "cd /workspace && git push origin main"
run_case "deny git clone"           2 "git clone https://github.com/foo/bar"

# Credential file references.
run_case "deny cat .git/config"     2 "cat .git/config"
run_case "deny cat netrc"           2 "cat ~/.netrc"
run_case "deny ls .ssh"             2 "ls ~/.ssh/"
run_case "deny grep token gitconf"  2 "grep token .git/config"

# /proc env/cmdline exfil.
run_case "deny /proc/self/environ" 2 "cat /proc/self/environ"
run_case "deny /proc/1/environ"     2 "cat /proc/1/environ"
run_case "deny redirect /proc env" 2 'read -r v < /proc/self/environ && echo "$v"'

# env / printenv / set / declare / compgen / export dumps.
run_case "deny bare env"            2 "env"
run_case "deny bare printenv"       2 "printenv"
run_case "deny bare set"            2 "set"
run_case "deny set piped"           2 "set | grep TOKEN"
run_case "deny declare -x"          2 "declare -x"
run_case "deny export -p"           2 "export -p"
run_case "deny compgen -v"          2 "compgen -v"
run_case "deny compgen -e"          2 "compgen -e"
run_case "deny typeset -p"          2 "typeset -p"

# Sourcing credential-bearing files.
run_case "deny source .env"         2 "source .env"
run_case "deny dot-source /etc/env" 2 ". /etc/environment"
run_case "deny source /proc env"    2 "source /proc/self/environ"

# Encoding tools on credential files.
run_case "deny base64 .env"         2 "base64 .env"
run_case "deny xxd netrc"           2 "xxd ~/.netrc"
run_case "deny strings .git/config" 2 "strings .git/config"
run_case "deny od -c gitconfig"     2 "od -c .git/config"

# Interpreter one-liners against cred paths.
run_case "deny python open .env"    2 "python3 -c 'print(open(\".env\").read())'"
run_case "deny perl read netrc"     2 "perl -e 'open(F,\".netrc\"); print <F>'"
run_case "deny node fs netrc"       2 "node -e 'console.log(require(\"fs\").readFileSync(\".netrc\",\"utf8\"))'"

# GitHub HTTP.
run_case "deny curl github"         2 "curl https://github.com/foo"
run_case "deny wget api.github"     2 "wget https://api.github.com/repos/foo"

# rm on system paths.
run_case "deny rm -rf /app"         2 "rm -rf /app/roboco"
run_case "deny rm -rf /etc"         2 "rm -rf /etc"

# ---------- ALLOW cases (exit 0) — must NOT be denied ----------
run_case "allow set -e"                 0 "set -e"
run_case "allow set -euo pipefail"      0 "set -euo pipefail"
run_case "allow set -o pipefail"        0 "set -o pipefail"
run_case "allow env VAR=val cmd"        0 "env FOO=bar uv run pytest"
run_case "allow env -i cmd"             0 "env -i HOME=/tmp ls /tmp"
run_case "allow ls"                     0 "ls -la /workspace"
run_case "allow uv run ruff"            0 "uv run ruff check ."
run_case "allow pnpm typecheck"         0 "pnpm typecheck"
run_case "allow rm in workspace"        0 "rm -rf /workspace/tmp"
run_case "allow declare -a arr"         0 "declare -a arr=(a b c)"
run_case "allow cat README"             0 "cat README.md"
run_case "allow curl non-github"        0 "curl https://example.com/info"

# ---------- Report ----------
echo
echo "===== bash-guard-hook tests ====="
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
