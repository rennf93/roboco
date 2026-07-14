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
# The grok CLI passes the same event with camelCase keys (toolName / toolInput);
# the extractor accepts either, so the one tested script guards both runtimes.
# Exit 0 = allow. Exit 2 = deny.
#
# ROBOCO_GUARD_SKIP_GIT=1 skips the git-ops category. The grok path sets it because
# grok handles git via NATIVE --deny rules, which deny GRACEFULLY (the agent gets
# a permission error and recovers) — whereas a grok hook deny CANCELS the whole
# run. So grok keeps git on --deny (operational reflex → recoverable) and uses
# this hook only for the exfil categories (no legit use → a hard cancel is the
# right response). Claude has no such --deny, so it keeps the git block here.
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
    ti = d.get("tool_input") or d.get("toolInput") or {}
    print(ti.get("command", ""))
except Exception:
    print("")
' 2>/dev/null)
[[ -z "$cmd" ]] && exit 0

low=$(printf '%s' "$cmd" | tr "[:upper:]" "[:lower:]")

# Skeletonize the command for the git-ops check ONLY: strip heredoc
# bodies and echo/printf literal arguments. Those are data the shell writes
# to a file, never commands the shell executes — so a README/heredoc that
# merely documents `git commit` must not be mistaken for invoking git.
# Quoted args to a shell interpreter (`bash -c "... && git fetch"`) ARE
# executed, are not echo/printf/heredoc bodies, and so survive untouched.
# Every other rule below still inspects the full command ($low).
git_skel=$(printf '%s' "$cmd" | python3 -c '
import sys, re
src = sys.stdin.read()
lines = src.split("\n")
opener = re.compile(r"<<-?\s*[^\sA-Za-z_]*([A-Za-z_]\w*)")
kept = []
i = 0
n = len(lines)
while i < n:
    line = lines[i]
    kept.append(line)
    m = opener.search(line)
    if m:
        delim = m.group(1)
        dash = "<<-" in line
        i += 1
        while i < n:
            body = lines[i]
            cand = body.strip() if dash else body
            if cand == delim:
                kept.append(body)
                break
            i += 1
    i += 1
skel = "\n".join(kept)
skel = re.sub(r"(^|[\n;&|]|&&|\|\|)\s*(echo|printf)\b[^\n;&|]*", r"\1", skel)
sys.stdout.write("__SKEL_OK__" + skel)
' 2>/dev/null)
# A successful run is prefixed with the sentinel even when the skeleton is
# legitimately empty (whole command was echo/heredoc). No sentinel means
# python failed — fail closed by inspecting the full command.
if [[ "$git_skel" == __SKEL_OK__* ]]; then
    git_skel="${git_skel#__SKEL_OK__}"
else
    git_skel="$cmd"
fi
git_skel_low=$(printf '%s' "$git_skel" | tr "[:upper:]" "[:lower:]")

# --- git network / auth ops ---------------------------------------------------
# Skipped on grok (handled by native --deny so a blocked git op is recoverable,
# not a run-cancelling hook deny). See the header.
if [[ "${ROBOCO_GUARD_SKIP_GIT:-}" != "1" ]] && \
   echo "$git_skel_low" | grep -qE '(^|[[:space:];&|])git[[:space:]]+(fetch|pull|push|clone|remote|ls-remote|checkout|commit|merge|rebase|reset|cherry-pick|revert|tag[[:space:]]+-d|update-ref|reflog[[:space:]]+delete)'; then
    echo "Denied: shell git for network / auth / branch-mutating ops is blocked." >&2
    echo "Use the verb listed in your role's State→Verb table (e.g. commit, complete, i_am_done)." >&2
    exit 2
fi

# --- git verbs hidden in command substitutions ($(...) / backticks) ----------
# The skeletonizer above strips echo/printf/heredoc DATA, but a command
# substitution inside that data is EXPANDED by the shell before the wrapping
# command runs — so `echo $(git push)` would slip past the git check above.
# Detect a denied git verb inside an EXPANDABLE substitution. Single-quoted
# strings (literal) and heredoc bodies (treated as data, matching the
# skeletonizer above) are excluded so a README that merely documents git verbs
# is not a false positive — this targets the echo/printf substitution class
# (`echo $(git push)`). Same SKIP_GIT guard as the check above — on grok this
# stays the native --deny's job, never a run-cancelling hook deny.
if [[ "${ROBOCO_GUARD_SKIP_GIT:-}" != "1" ]]; then
    subst_git=$(printf '%s' "$cmd" | python3 -c '
import sys, re
src = sys.stdin.read()
q = chr(39)
lines = src.split(chr(10))
opener = re.compile(r"<<-?\s*[^\sA-Za-z_]*([A-Za-z_]\w*)")
kept = []
i = 0
n = len(lines)
while i < n:
    line = lines[i]
    kept.append(line)
    m = opener.search(line)
    if m:
        delim = m.group(1)
        dash = "<<-" in line
        i += 1
        while i < n:
            cand = lines[i].strip() if dash else lines[i]
            if cand == delim:
                kept.append(lines[i])
                break
            i += 1
    i += 1
text = chr(10).join(kept)
text = re.sub(q + "[^" + q + "]*" + q, " ", text)
bt = chr(96)
verbs = r"(fetch|pull|push|clone|remote|ls-remote|checkout|commit|merge|rebase|reset|cherry-pick|revert|tag\s+-d|update-ref|reflog\s+delete)"
gitre = re.compile(r"(^|[\s;&|()$" + bt + r"])git\s+" + verbs, re.IGNORECASE)
subst = re.compile(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)|" + bt + r"([^" + bt + r"]*)" + bt, re.DOTALL)
deny = "no"
for m in subst.finditer(text):
    inner = m.group(1) or m.group(2) or ""
    if gitre.search(inner):
        deny = "yes"
        break
sys.stdout.write("__OK__" + deny)
' 2>/dev/null)
    # Fail closed: anything other than the clean sentinel (incl. a python
    # failure that yields an empty string) is treated as a deny.
    if [[ "$subst_git" != "__OK__no" ]]; then
        echo "Denied: a git verb inside a command substitution (\$(...) or backticks) is evaluated by the shell before the wrapping echo/printf/heredoc runs." >&2
        echo "Use the verb listed in your role's State→Verb table (e.g. commit, complete, i_am_done)." >&2
        exit 2
    fi
fi

# --- credential / secret exfil ------------------------------------------------
# Block ANY bash command that references a credential file path — catches
# `cat .git/config`, `python -c "open('.git/config')..."`, `grep token .git/
# config`, `strings ~/.netrc`, etc. The token is scrubbed from .git/config
# post-clone so the file is uninteresting, but a leaked PAT is unrecoverable
# so belt + suspenders applies.
#
# .credentials.json / .claude.json: the host's Claude Code OAuth credential
# store (~/.claude, ~/.claude.json) is bind-mounted read-write into every
# agent container — the shared subscription auth every spawned agent uses.
# No agent role's job ever needs to read its own harness's auth, so treat it
# as a credential file like .netrc/.git-credentials above.
if echo "$low" | grep -qE '(\.git/config|\.gitconfig|\.git-credentials|\.netrc|\.ssh/|id_rsa|id_ed25519|id_ecdsa|known_hosts|\.credentials\.json|\.claude\.json)'; then
    echo "Denied: command references a credential file or SSH key. Don't read git credentials or the harness's Claude Code auth — the PAT is injected subprocess-side by the MCP layer (commit / complete verbs) and never lands in these files." >&2
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

# --- remote code execution: piping a fetched payload straight into a shell ---
# `curl url | sh` (or bash/zsh/dash/ksh), `bash <(curl url)`, and
# `eval "$(curl url)"` all execute untrusted remote content regardless of the
# destination host — the github-specific and internal-host checks above only
# gate specific DESTINATIONS, so `curl https://raw.githubusercontent.com/... |
# bash` (not github.com itself) or any other external host was a blind spot.
# Scoped to actual shells only (sh/bash/zsh/dash/ksh) — piping into a
# non-executing consumer (`curl url | tar xz`, `curl url | jq`, `curl url -o
# file`) is untouched and still allowed; there's no legitimate reason to feed
# a shell interpreter's stdin from a network fetch.
if echo "$low" | grep -qE '(curl|wget|httpie)\b[^|;&]*\|[[:space:]]*(sudo[[:space:]]+)?(sh|bash|zsh|dash|ksh)([[:space:]]|$)'; then
    echo "Denied: piping a downloaded payload straight into a shell executes untrusted remote code. Download to a file and inspect it, or use your normal toolchain (uv / pnpm) to install a package." >&2
    exit 2
fi
if echo "$low" | grep -qE '(^|[[:space:];&|])(sh|bash|zsh|dash|ksh|source|\.)[[:space:]]+<\([[:space:]]*(curl|wget)\b'; then
    echo "Denied: process-substitution execution of a curl/wget payload runs untrusted remote code." >&2
    exit 2
fi
if echo "$low" | grep -qE 'eval[[:space:]]+"?\$\([[:space:]]*(curl|wget)\b'; then
    echo "Denied: eval of a curl/wget payload runs untrusted remote code." >&2
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
# Interpreter / library-driven HTTP is handled by the rule below.
# KNOWN GAP (still out of scope here):
#   - Variable expansion: `URL=http://orchestrator/x; curl $URL` — the guard
#     sees `curl $URL`, not the expanded URL, so this slips through. The
#     server-side X-Agent-Role check is the second gate.
if echo "$low" | grep -qE '(^|[[:space:];&|])(curl|wget|http|https|httpie)[[:space:]]' && \
   echo "$low" | grep -qE '((http|https)://)?/?(roboco-[a-z0-9_-]+|localhost|127\.0\.0\.1|0\.0\.0\.0)[:/]'; then
    echo "Denied: internal API calls bypass the gateway. Use the MCP verbs (roboco-flow / roboco-do / roboco-git-readonly / roboco-optimal / roboco-docs) — they route through the orchestrator with the right auth and tracing." >&2
    exit 2
fi

# --- interpreter/library HTTP to an internal host ----------------------------
# The curl/wget rule above only fires when the FIRST token is an HTTP CLI.
# A live run showed an agent reach the orchestrator with forged X-Agent-*
# identity headers via:
#   python3 << 'EOF'
#   import httpx
#   httpx.post("http://roboco-orchestrator:8000/api/v2/flow/developer/i_will_work_on",
#              headers={"X-Agent-ID": "<self>", "X-Agent-Role": "developer"})
#   EOF
# The binary is python3 (slips the CLI check) and it imports httpx, not
# roboco.* (slips the roboco-internals import check). Close it language-agnostically:
# deny when the command pairs an HTTP-client token with a forbidden
# internal host. The whole command (heredoc body included) is in $low,
# consistent with the curl/wget sibling above. Legitimate shell work does
# not both name an internal host AND drive an HTTP client; external HTTP
# (pypi, docs.python.org, github — github also hits its own rule earlier)
# has no internal host so it still passes.
if echo "$low" | grep -qE '(httpx|requests|urllib|aiohttp|http\.client|httplib|http\.request|net/http|net::http|httparty|faraday|lwp|libwww|httpurlconnection|okhttp|node-fetch|axios|xmlhttprequest|websocket|fetch[[:space:]]*\()' && \
   echo "$low" | grep -qE '((http|https|ws|wss)://)?/?(roboco-[a-z0-9_-]+|localhost|127\.0\.0\.1|0\.0\.0\.0)[:/]'; then
    echo "Denied: reaching an internal host via an HTTP client (httpx / requests / urllib / aiohttp / fetch / Net::HTTP / ...) bypasses the gateway, role manifest, tracing and auth — and lets you forge X-Agent-* identity headers. Use your role's MCP verbs (roboco-flow / roboco-do / roboco-git-readonly / roboco-optimal / roboco-docs); they are the only sanctioned path to the orchestrator." >&2
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
if echo "$low" | grep -qE '(^|[[:space:];&|])(source|\.)[[:space:]]+[^|;&]*(\.env|/etc/environment|/proc/[^[:space:]]*environ|\.profile|\.bashrc|\.zshrc|\.git/config|\.netrc|\.credentials\.json|\.claude\.json)'; then
    echo "Denied: sourcing credential-bearing files exposes secrets in the current shell." >&2
    exit 2
fi

# Binary/encoding tools pointed at credential files — catches `base64 .env`,
# `xxd ~/.netrc`, `strings .git/config`, `od -c .git-credentials`, etc.
if echo "$low" | grep -qE '(^|[[:space:];&|])(base64|od|xxd|hexdump|strings|uuencode)[[:space:]]+[^|;&]*(\.env|\.git/config|\.gitconfig|\.git-credentials|\.netrc|\.ssh/|id_rsa|id_ed25519|\.credentials\.json|\.claude\.json)'; then
    echo "Denied: encoding/inspecting a credential file is still exfiltration." >&2
    exit 2
fi

# Interpreter one-liners reading credential paths.
if echo "$low" | grep -qE '(^|[[:space:];&|])(python3?|perl|node|ruby|awk|sed)[[:space:]]+[^|;&]*-[ce][[:space:]]+[^|;&]*(\.env|\.git/config|\.gitconfig|\.git-credentials|\.netrc|/proc/[^[:space:]]*environ|id_rsa|id_ed25519|\.credentials\.json|\.claude\.json)'; then
    echo "Denied: interpreter snippet reads a credential file. Ask orchestrator for the value you need." >&2
    exit 2
fi

# --- gateway-internals import bypass ------------------------------------------
# An agent must reach the orchestrator ONLY through its manifest-bound MCP
# verbs. Importing the server package directly
#   uv run python3 -c "from roboco.mcp.flow_server import open_pr; open_pr(...)"
#   python3 << 'EOF' ... import roboco.services.gateway ... EOF
#   python -m roboco.mcp.do_server
# bypasses the per-role tool manifest entirely (role-scoping becomes
# meaningless if the agent can call any verb in-process) and lets the agent
# run choreographer/service code outside the gateway's tracing + auth.
# The whole command string (heredoc body included) is in $low, so a flat
# substring match on a roboco import is sufficient and robust to quoting.
if echo "$low" | grep -qE '(python3?|uv[[:space:]]+run|poetry[[:space:]]+run|pipenv[[:space:]]+run|pdm[[:space:]]+run|hatch[[:space:]]+run)' && \
   echo "$low" | grep -qE '(import[[:space:]]+roboco|from[[:space:]]+roboco|-m[[:space:]]+roboco|roboco\.(mcp|services|runtime|foundation|api|enforcement)\b)'; then
    echo "Denied: importing or running roboco.* internals from the shell bypasses the MCP role manifest, tracing, and auth. Use your role's MCP verbs (roboco-flow / roboco-do / roboco-git-readonly / roboco-optimal / roboco-docs) — they are the only sanctioned path to the orchestrator." >&2
    exit 2
fi

# --- agent-identity forgery --------------------------------------------------
# ROBOCO_AGENT_ID is the agent's identity. It is injected by the orchestrator
# at spawn and the agent process must never rewrite it — doing so lets one
# agent act as another (forged audit trail, bypassed ownership checks). No
# legitimate agent shell command sets this variable; deny any assignment or
# export of it (already lowercased into $low).
if echo "$low" | grep -qE '(^|[[:space:];&|]|env[[:space:]]+|export[[:space:]]+)roboco_agent_id[[:space:]]*='; then
    echo "Denied: ROBOCO_AGENT_ID is your injected identity — overriding it forges another agent's identity. Never set or export it. Call your MCP verbs with your real identity instead." >&2
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

# --- package-environment mutations targeting /app (ALL providers) -------------
# /app holds the orchestrator code and the MCP-gateway venv (/app/.venv). An
# agent that `uv sync` / `pip install`s into /app rebuilds that venv and breaks
# its OWN gateway tools (every roboco-flow / -do / -git verb) — stranding the
# agent and getting its task reaped. Agents manage dependencies in their
# workspace clone under /data/workspaces, never in /app. Two-step: a
# package-mutation verb AND a target that resolves to /app's environment
# (cd /app, --project/--directory /app, /app/.venv, UV_PROJECT_ENVIRONMENT=/app).
# Reads of /app (cat/ls/grep) and workspace installs are untouched. Deliberately
# NOT gated by ROBOCO_GUARD_SKIP_GIT, so it fires for every provider — the Claude
# PreToolUse hook AND the grok exfil hook (and future provider hooks).
if echo "$low" | grep -qE '(^|[[:space:];&|])(uv[[:space:]]+(sync|lock|add|remove)|uv[[:space:]]+pip[[:space:]]+(install|uninstall)|pip3?[[:space:]]+(install|uninstall))' && \
   echo "$low" | grep -qE '(/app/\.venv|--project[[:space:]=]+"?/app([^a-z]|$)|--directory[[:space:]=]+"?/app([^a-z]|$)|uv_project_environment="?/app([^a-z]|$)|(^|[[:space:];&|])cd[[:space:]]+"?/app([^a-z]|$))'; then
    echo "Denied: installing or syncing packages into /app rebuilds the orchestrator / MCP-gateway venv (/app/.venv) and breaks your own gateway tools. Manage dependencies in your workspace clone under /data/workspaces, never /app. If /app's environment looks broken, report it via your blocked / escalation verb — don't try to repair it." >&2
    exit 2
fi

# `uv run --active` is denied as a footgun: the contract is bare `uv run`
# (workspace .venv, cwd-relative). VIRTUAL_ENV is no longer image-baked (it
# leaked into every workspace `uv run` as a warning), so --active has no active
# env and errors; an explicit /app target still bricks the gateway (next block).
# Bare `uv run` (workspace .venv, cwd-relative) is untouched.
if echo "$low" | grep -qE '(^|[[:space:];&|])uv[[:space:]]+run([[:space:]]|$)' && \
   echo "$low" | grep -qE '(^|[[:space:]=])--active([[:space:]]|$)'; then
    echo "Denied: \`uv run --active\` is not the contract — use bare \`uv run\` (it uses your workspace .venv under /data/workspaces, never /app). If /app's environment looks broken, report it via your blocked / escalation verb." >&2
    exit 2
fi
if echo "$low" | grep -qE '(^|[[:space:];&|])(uv[[:space:]]+run|uvx)([[:space:]]|$)' && \
   echo "$low" | grep -qE '(/app/\.venv|--project[[:space:]=]+"?/app([^a-z]|$)|--directory[[:space:]=]+"?/app([^a-z]|$)|uv_project_environment="?/app([^a-z]|$)|(^|[[:space:];&|])cd[[:space:]]+"?/app([^a-z]|$))'; then
    echo "Denied: running uv against /app targets the image-baked MCP-gateway venv (/app/.venv) and rebuilds it, bricking your own gateway tools. Use bare \`uv run\` from your workspace clone under /data/workspaces. If /app's environment looks broken, report it via your blocked / escalation verb." >&2
    exit 2
fi

# --- raw package-manager / test-runner commands — use the Makefile -----------
# CEO direction: force the fleet to the Makefile. The blocks above deliberately
# allowed bare `uv run` (workspace .venv); this overrides that when a Makefile is
# present, denying raw uv/pip/conda/poetry and remediating to the make targets.
# The Makefile sets UV_NO_SYNC=1 + a private UV_CACHE_DIR for consistent gate
# behaviour; bare `uv run` bypasses both. Skipped when no Makefile exists so
# Makefile-less projects aren't blocked. `make`-internal uv (hook inspects the
# agent's command string, not subprocesses) and WorkspaceService's uv sync
# (subprocess, not the agent Bash tool) are untouched. On grok a deny cancels
# the whole run, so ROBOCO_GUARD_SKIP_PM=1 nudges (exit 0) instead.
if test -f Makefile && echo "$low" | grep -qE '(^|[[:space:];&|])(uv[[:space:]]+(run|pip[[:space:]]+(install|uninstall)|lock|add|remove)|pip3?[[:space:]]+(install|uninstall)|conda[[:space:]]+(install|create|run)|poetry[[:space:]]+(run|install|add))([[:space:]]|$)'; then
    if [ -n "${ROBOCO_GUARD_SKIP_PM:-}" ]; then
        echo "Nudge: raw package-manager commands are blocked — use \`make quality\` / \`make gate\` / \`make lint\` / \`make test\`. The Makefile sets UV_NO_SYNC=1 + a private cache; bare \`uv run\` bypasses that." >&2
        exit 0
    fi
    echo "Denied: raw package-manager commands are blocked — use the Makefile. Run \`make quality\` (full gate), \`make gate\` (fast pre-submit), \`make lint\`, or \`make test\`. The Makefile sets UV_NO_SYNC=1 + a private cache to prevent venv corruption; bare \`uv run\` bypasses that." >&2
    exit 2
fi

exit 0
