// opencode plugin — command guard / secret-scrub for RoboCo Grok agents.
//
// Ports the security-critical deny rules from docker/scripts/bash-guard-hook.sh
// (the Claude Code PreToolUse guard) to opencode's `tool.execute.before` hook.
// Those rules are Claude Code hooks and do NOT transfer to the opencode runtime,
// so a Grok agent would otherwise run bash unguarded — this restores parity.
//
// Mechanism (confirmed by opencode's own env-protection plugin example):
// throwing inside `tool.execute.before` denies the tool call. For `bash` the
// command is `output.args.command`; for `read`/`edit` the path is
// `output.args.filePath`.
//
// Loaded via the generated opencode.json `plugin:` array (see
// roboco.llm.providers.opencode_config). The agent's bash permission can also
// be set to "deny"/"ask" via ROBOCO_GROK_BASH_PERMISSION as a second gate.
//
// STATUS: unvalidated against a live opencode runtime. Confirm it actually
// fires in the live E2E spawn before pointing a Grok dev-agent at a real repo.
// Deny-on-match is fail-closed: a false positive blocks a legitimate command
// (annoying, safe) rather than letting a dangerous one through.

const CREDENTIAL_FILE =
  /(\.git\/config|\.gitconfig|\.git-credentials|\.netrc|\.ssh\/|id_rsa|id_ed25519|id_ecdsa|known_hosts)/;

const INTERNAL_HOST =
  /((https?|wss?):\/\/)?\/?(roboco-[a-z0-9_-]+|localhost|127\.0\.0\.1|0\.0\.0\.0)[:/]/;

const HTTP_CLIENT_LIB =
  /(httpx|requests|urllib|aiohttp|http\.client|httplib|net\/http|net::http|node-fetch|axios|xmlhttprequest|websocket|fetch\s*\()/;

// Each check takes the lowercased bash command and returns a deny reason, or
// null to allow. Mirrors the categories in bash-guard-hook.sh.
const BASH_CHECKS = [
  (low) =>
    /(^|[\s;&|])git\s+(fetch|pull|push|clone|remote|ls-remote|checkout|commit|merge|rebase|reset|cherry-pick|revert|tag\s+-d|update-ref)/.test(
      low,
    )
      ? "shell git for network/auth/branch-mutating ops is blocked — use your role's MCP verb (commit, complete, i_am_done, ...)."
      : null,
  (low) =>
    CREDENTIAL_FILE.test(low)
      ? "command references a credential file or SSH key — the PAT is injected subprocess-side by the MCP layer, never read from these files."
      : null,
  (low) =>
    /\/proc\/(self|\d+|\$\$)\/(environ|cmdline|cwd|exe)/.test(low)
      ? "reading /proc/*/environ or /proc/*/cmdline can leak credentials."
      : null,
  (low) =>
    /(^|[\s;&|])(curl|wget|http|https|httpie)\s[^|]*(github\.com|api\.github\.com)/.test(
      low,
    )
      ? "direct GitHub HTTP calls bypass the PAT handler — use the role-appropriate MCP verb."
      : null,
  (low) =>
    /(^|[\s;&|])(curl|wget|http|https|httpie)\s/.test(low) && INTERNAL_HOST.test(low)
      ? "internal API calls bypass the gateway — use the MCP verbs (roboco-flow / roboco-do / roboco-git-readonly / roboco-optimal)."
      : null,
  (low) =>
    HTTP_CLIENT_LIB.test(low) && INTERNAL_HOST.test(low)
      ? "reaching an internal host via an HTTP client bypasses the gateway, role manifest, tracing and auth (and can forge X-Agent-* headers). Use your MCP verbs."
      : null,
  (low) =>
    /(python3?|uv\s+run|poetry\s+run|pipenv\s+run|pdm\s+run|hatch\s+run)/.test(low) &&
    /(import\s+roboco|from\s+roboco|-m\s+roboco|roboco\.(mcp|services|runtime|foundation|api|enforcement)\b)/.test(
      low,
    )
      ? "importing or running roboco.* internals from the shell bypasses the MCP role manifest, tracing and auth. Use your role's MCP verbs."
      : null,
  (low) =>
    /(^|[\s;&|]|env\s+|export\s+)roboco_agent_id\s*=/.test(low)
      ? "ROBOCO_AGENT_ID is your injected identity — overriding it forges another agent's identity. Never set or export it."
      : null,
  (low) =>
    /(^|[\s;&|])(env|printenv)([\s]|$)/.test(low) &&
    !/(^|[\s;&|])env\s+(-i|[a-z_][a-z0-9_]*=)/.test(low)
      ? "env / printenv can leak secrets. Ask for the specific value you need via the task description."
      : null,
  (low) =>
    /(^|[\s;&|])set([\s]*$|[\s]*[|;&])/.test(low) ||
    /(^|[\s;&|])(declare|typeset)\s+-[a-z]*[xp]/.test(low) ||
    /(^|[\s;&|])export\s+-p([\s]|$)/.test(low) ||
    /(^|[\s;&|])compgen\s+-[a-z]*[ve]/.test(low)
      ? "shell built-ins that dump variables/exports can leak credentials."
      : null,
  (low) =>
    /(^|[\s;&|])(base64|od|xxd|hexdump|strings|uuencode)\s[^|;&]*(\.env|\.git\/config|\.gitconfig|\.git-credentials|\.netrc|\.ssh\/|id_rsa|id_ed25519)/.test(
      low,
    )
      ? "encoding/inspecting a credential file is still exfiltration."
      : null,
  (low) =>
    /(^|[\s;&|])rm\s[^|;&]*-[a-z]*[rf][a-z]*\s/.test(low) &&
    /(^|[\s;&|])rm\s[^|;&]*(\/app($|[\s/])|\/root|\/etc|\/var|\/usr|\/bin|\/sbin|\/lib|\/home|\s\/\s*(;|\||&|$))/.test(
      low,
    )
      ? "rm on a system path. Operate inside your own workspace only."
      : null,
];

// Tools that take a file path we must keep away from credential files.
const PATH_TOOLS = new Set(["read", "edit", "write"]);

function denyBash(command) {
  const low = String(command || "").toLowerCase();
  if (!low) return null;
  for (const check of BASH_CHECKS) {
    const reason = check(low);
    if (reason) return reason;
  }
  return null;
}

export default async () => {
  return {
    "tool.execute.before": async (input, output) => {
      const tool = input?.tool;
      const args = output?.args || {};
      if (tool === "bash") {
        const reason = denyBash(args.command);
        if (reason) throw new Error(`Denied by roboco secret-scrub: ${reason}`);
        return;
      }
      if (PATH_TOOLS.has(tool)) {
        const path = String(args.filePath || args.path || "").toLowerCase();
        if (path && CREDENTIAL_FILE.test(path)) {
          throw new Error(
            "Denied by roboco secret-scrub: access to a credential file / SSH key is blocked.",
          );
        }
      }
    },
  };
};
