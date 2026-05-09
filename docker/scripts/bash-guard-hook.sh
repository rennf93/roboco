#!/bin/bash
# PreToolUse guard for Bash.
#
# Deny-listed patterns in base_deny (permissions.deny) only match the first
# binary in a Bash command. Compound commands like
#   bash -c "cd /workspace && git fetch origin"
# slip through because the first token is `cd`. This hook inspects the full
# command string and rejects any shell-level git network/auth op, redirecting
# the agent to the MCP equivalent.
#
# Claude Code passes the PreToolUse event on stdin as JSON:
#   { "tool_name": "Bash", "tool_input": { "command": "...", "description": "..." } }
# Exit 0 = allow. Exit 2 = deny with message on stdout.
#
# Deny categories:
#   - Network git ops (require token injection only done by the MCP layer)
#   - Shell-level PR / merge that bypass the PM hierarchy
#   - Credential exfiltration vectors the existing deny list doesn't catch
#     (compound cat/env/curl/wget)

set -u

input=$(cat 2>/dev/null || true)
[[ -z "$input" ]] && exit 0

cmd=$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
    ti = d.get("tool_input", {}) or {}
    print(ti.get("command", ""))
except Exception:
    print("")
' 2>/dev/null)
[[ -z "$cmd" ]] && exit 0

low=$(printf '%s' "$cmd" | tr "[:upper:]" "[:lower:]")

# --- git network / auth ops ---------------------------------------------------
if echo "$low" | grep -qE '(^|[[:space:];&|])git[[:space:]]+(fetch|pull|push|clone|remote|ls-remote|checkout|commit|merge|rebase|reset|cherry-pick|revert|tag[[:space:]]+-d|update-ref|reflog[[:space:]]+delete)'; then
    cat <<'EOF' >&2
Denied: shell git for network / auth / branch-mutating ops is blocked.

Read-only inspection (any role):
  - roboco-git-readonly MCP: roboco_git_status / _log / _diff / _branch_list

Write paths — there is NO direct shell-git or "roboco_git_commit" tool.
Use the verb that matches your role; the choreographer handles git for you:

  - developer / documenter: roboco-do `commit(message, files)`
      → auto-prefixes [task-id], pushes to your branch, opens a PR via
        the choreographer when the task transitions out of in_progress.
        Your branch is auto-created when you call i_will_work_on().

  - cell_pm / main_pm: roboco-flow `complete(task_id, notes)`
      → cell_pm merges the leaf PR; main_pm opens the master PR and
        escalates to CEO. PMs never run git directly — they delegate
        code work to devs and complete to merge.

  - any role: branches are NOT something you set up. They are created
    on claim/i_will_work_on. If you don't see your branch, check that
    you're actually claimed on the task.

Raw `git fetch` etc. don't have auth (the PAT is injected only inside
the MCP layer) and will fail with "could not read Username for
'https://github.com'".
EOF
    exit 2
fi

# --- credential / secret exfil ------------------------------------------------
# Block ANY bash command that references a credential file path — catches
# `cat .git/config`, `python -c "open('.git/config')..."`, `grep token .git/
# config`, `strings ~/.netrc`, etc. The token is scrubbed from .git/config
# post-clone so the file is uninteresting, but a leaked PAT is unrecoverable
# so belt + suspenders applies.
if echo "$low" | grep -qE '(\.git/config|\.gitconfig|\.git-credentials|\.netrc|\.ssh/|id_rsa|id_ed25519|id_ecdsa|known_hosts)'; then
    echo "Denied: command references a credential file or SSH key. Don't read git credentials — the PAT is injected subprocess-side by the MCP layer (commit / complete verbs) and never lands in these files." >&2
    exit 2
fi

# /proc-based env/secret exfil: /proc/<pid>/environ, /proc/self/environ, etc.
if echo "$low" | grep -qE '/proc/(self|[0-9]+|\$\$|\$\{.*\})/(environ|cmdline|cwd|exe)'; then
    echo "Denied: reading /proc/*/environ or /proc/*/cmdline can leak credentials from another process. Ask the orchestrator for the specific value you need." >&2
    exit 2
fi

if echo "$low" | grep -qE '(^|[[:space:];&|])(curl|wget|http|https)[[:space:]][^|]*(github\.com|api\.github\.com)'; then
    echo "Denied: direct GitHub HTTP calls bypass the PAT handler. Use the role-appropriate MCP verb: roboco-do commit (devs/docs), roboco-flow complete (PMs), or roboco-git-readonly status/log/diff/branch_list (any role)." >&2
    exit 2
fi

# --- internal API calls -------------------------------------------------------
# Agents must reach the orchestrator through their MCP manifest verbs, never
# raw HTTP. Two-step check: (a) is this a curl/wget/http/https/httpie command,
# AND (b) does the line reference a forbidden internal host. Both must match.
# This catches all forms uniformly:
#   - scheme-ful:         `curl http://roboco-orchestrator:8000/api`
#   - scheme-less:        `curl roboco-orchestrator:8000/api`
#   - protocol-relative:  `curl //roboco-orchestrator:8000/api`
#   - any flag ordering:  `curl -s -X POST http://localhost:8000/x -d ...`
# KNOWN GAPS (out of scope here):
#   - Variable expansion: `URL=http://orchestrator/x; curl $URL` — the guard
#     sees `curl $URL`, not the expanded URL, so this slips through. The
#     X-Agent-Role check (task 4) is the second gate.
#   - Interpreter one-liners: `python -c "import urllib.request; ..."` — too
#     deeply hidden to regex. Mitigated by the manifest-bound MCP surface.
if echo "$low" | grep -qE '(^|[[:space:];&|])(curl|wget|http|https|httpie)[[:space:]]' && \
   echo "$low" | grep -qE '((http|https)://)?/?(roboco-[a-z0-9_-]+|localhost|127\.0\.0\.1|0\.0\.0\.0)[:/]'; then
    echo "Denied: internal API calls bypass the gateway. Use the MCP verbs (roboco-flow / roboco-do / roboco-git-readonly / roboco-optimal / roboco-docs) — they route through the orchestrator with the right auth and tracing." >&2
    exit 2
fi

if echo "$low" | grep -qE '(^|[[:space:];&|])(env|printenv)([[:space:]]|$)' && ! echo "$low" | grep -qE '(^|[[:space:];&|])env[[:space:]]+-i'; then
    # allow `env VAR=val cmd` style prefixes (`env ` followed by `NAME=`)
    if ! echo "$low" | grep -qE '(^|[[:space:];&|])env[[:space:]]+[a-z_][a-z0-9_]*='; then
        echo "Denied: env / printenv can leak secrets. Ask for the specific value you need via the task description." >&2
        exit 2
    fi
fi

# Shell built-ins that dump variables / exported env. `set -e`, `set -u`,
# `set -o pipefail` etc. must still pass — so we only deny `set` with no args
# or followed by a terminator (`|`, `;`, `&&`, newline/EOL).
if echo "$low" | grep -qE '(^|[[:space:];&|])set([[:space:]]*$|[[:space:]]*[|;&])'; then
    echo "Denied: bare \`set\` dumps all shell variables including exported credentials." >&2
    exit 2
fi
if echo "$low" | grep -qE '(^|[[:space:];&|])(declare|typeset)[[:space:]]+-[[:alpha:]]*[xp]'; then
    echo "Denied: \`declare -x\` / \`typeset -p\` dumps exported variables." >&2
    exit 2
fi
if echo "$low" | grep -qE '(^|[[:space:];&|])export[[:space:]]+-p([[:space:]]|$)'; then
    echo "Denied: \`export -p\` dumps exported variables." >&2
    exit 2
fi
if echo "$low" | grep -qE '(^|[[:space:];&|])compgen[[:space:]]+-[[:alpha:]]*[ve]'; then
    echo "Denied: \`compgen -v\` / \`compgen -e\` enumerates variables/exports." >&2
    exit 2
fi

# Sourcing credential-bearing files via `source` or `.` dot-sourcing.
if echo "$low" | grep -qE '(^|[[:space:];&|])(source|\.)[[:space:]]+[^|;&]*(\.env|/etc/environment|/proc/[^[:space:]]*environ|\.profile|\.bashrc|\.zshrc|\.git/config|\.netrc)'; then
    echo "Denied: sourcing credential-bearing files exposes secrets in the current shell." >&2
    exit 2
fi

# Binary/encoding tools pointed at credential files — catches `base64 .env`,
# `xxd ~/.netrc`, `strings .git/config`, `od -c .git-credentials`, etc.
if echo "$low" | grep -qE '(^|[[:space:];&|])(base64|od|xxd|hexdump|strings|uuencode)[[:space:]]+[^|;&]*(\.env|\.git/config|\.gitconfig|\.git-credentials|\.netrc|\.ssh/|id_rsa|id_ed25519)'; then
    echo "Denied: encoding/inspecting a credential file is still exfiltration." >&2
    exit 2
fi

# Interpreter one-liners reading credential paths.
if echo "$low" | grep -qE '(^|[[:space:];&|])(python3?|perl|node|ruby|awk|sed)[[:space:]]+[^|;&]*-[ce][[:space:]]+[^|;&]*(\.env|\.git/config|\.gitconfig|\.git-credentials|\.netrc|/proc/[^[:space:]]*environ|id_rsa|id_ed25519)'; then
    echo "Denied: interpreter snippet reads a credential file. Ask orchestrator for the value you need." >&2
    exit 2
fi

# Redirected reads from /proc/self/environ: `read -r var < /proc/self/environ`,
# `while read … < /proc/…/environ`, etc.
if echo "$low" | grep -qE '<[[:space:]]*/proc/(self|[0-9]+)/(environ|cmdline)'; then
    echo "Denied: redirecting from /proc/*/environ leaks credentials." >&2
    exit 2
fi

# --- destructive ops on system paths ------------------------------------------
# Agents should only rm -rf inside their own workspace. Block system paths
# outright. Cross-workspace rm isn't regex-decidable here (we don't know the
# agent's slug at hook time) — defer that to the Write(workspace/**) allow
# list + file-ownership at the OS level.
if echo "$low" | grep -qE '(^|[[:space:];&|])rm[[:space:]]+[^|;&]*-[[:alpha:]]*[rRf][[:alpha:]]*[[:space:]]'; then
    if echo "$low" | grep -qE '(^|[[:space:];&|])rm[[:space:]]+[^|;&]*(/app($|[[:space:]/])|/root|/etc|/var|/usr|/bin|/sbin|/lib|/home)'; then
        echo "Denied: rm on a system path. Operate inside your own workspace only." >&2
        exit 2
    fi
    if echo "$low" | grep -qE '(^|[[:space:];&|])rm[[:space:]]+[^|;&]*[[:space:]]/[[:space:]]*(;|\||&|$)'; then
        echo "Denied: rm on root filesystem." >&2
        exit 2
    fi
fi

exit 0
