# LLM provider security posture

RoboCo routes each agent to one of several LLM providers (the **Routing** card in the control panel). This note states — truthfully — what protections an agent gets on each provider. The short version: **Grok now reaches effective security parity** — the command/secret-exfiltration guard, the budget/cost cap, and the prompt-injection guard all apply to Grok agents. The only Claude hook without a Grok equivalent is the stop-guard (terminal-verb enforcement), which is a workflow nicety, not a safety control. Any agent — including the delivery roles — can be routed to Grok.

## Two runtimes, not five

An agent has two layers: the **model** (the brain) and the **runtime** (the agent program that drives it — reads files, calls tools, loops). RoboCo's guardrails are implemented as runtime hooks, so they only exist when the runtime provides them.

| Provider (routing mode) | Runtime | Model |
|---|---|---|
| Anthropic | Claude Code | Claude (opus/sonnet) |
| Ollama (Cloud) | Claude Code (via `ANTHROPIC_BASE_URL` injection) | the Ollama model |
| Self-Hosted | Claude Code (via `ANTHROPIC_BASE_URL` injection) | your endpoint's model |
| **Grok (xAI)** | **grok CLI** (Grok Build) | **grok-build** |

Anthropic, Ollama, and Self-Hosted all run on **Claude Code** and therefore keep the **full guard set**. Only **Grok** runs on a different runtime — xAI's official **grok CLI** — because the `claude` binary rejects any non-Claude model id, so Grok cannot run inside Claude Code. The grok CLI is to Grok what Claude Code is to Claude, and (being Claude-Code-compatible) it supports the same blocking `PreToolUse` hook mechanism RoboCo's command guard relies on.

## Guardrail parity matrix

| Guardrail | Claude Code runtime (Anthropic / Ollama / Self-Hosted) | Grok (grok CLI) |
|---|---|---|
| MCP gateway + role tool-manifest | yes | yes (mounted by construction) |
| Command / secret-exfiltration guard (bash, credential files, internal-host calls, PAT exfil) | yes (`bash-guard-hook.sh`, PreToolUse) | **yes** — the SAME `bash-guard-hook.sh` installed as a grok blocking `PreToolUse` hook (`~/.grok/hooks/roboco-bash-guard.json`) for the exfil/credential/identity-forgery patterns, plus native `--deny` rules for git network/branch/history ops |
| Budget / runaway-cost kill-switch | yes (`post-tool-budget` hook against the SDK server) | **yes** — orchestrator-side cost watchdog (`ROBOCO_GROK_MAX_COST_USD`) reading the captured `usage.json` |
| Prompt-injection guard (rejects "ignore previous instructions", role-override, fake escalations in incoming A2A / task / notification content) | yes (`user-prompt-hook.sh`, UserPromptSubmit, denies the turn) | **yes** — recreated at RoboCo's input boundary (`prompt_guard.detect_injection`): the interactive driver scans every turn, the one-shot grok entrypoint scans the task prompt. Same patterns as the bash hook, kept in sync — independent of any runtime pre-prompt hook |
| Stop-guard (terminal-verb enforcement before a run ends) | yes (`stop-hook.sh`, Stop) | **no** — the grok CLI's `Stop` event is observe-only / non-blocking (workflow nicety, not a security control) |

### Why the command guard splits git from exfil on Grok

The grok CLI deny mechanisms differ in one important way. Its native `--deny` rules deny **gracefully** — a blocked command returns a permission error and the agent recovers (adapts to the gateway verb). Its `PreToolUse` hook deny instead **cancels the whole run**. So git ops (a reflexive `git push` an agent must be able to recover from) stay on native `--deny`, while the exfil/credential patterns (a credential read or identity forgery — which no legitimate agent does) go through the hook, where a hard cancel is the correct response. The one shared script handles both: the hook runs with `ROBOCO_GUARD_SKIP_GIT=1` so it leaves git to `--deny`. (`--deny` matches a command prefix only, so the bash-guard's compound-command analysis applies on the Claude path; on Grok the exfil categories are still hook-analysed, and the PAT boundary is covered server-side regardless — PAT scrubbing, the role manifest, and X-Agent-* identity checks.)

## The remaining gap: the stop-guard

Every *security-relevant* Claude guard now applies to Grok — command/secret-exfiltration (the bash-guard hook + git `--deny`), budget/runaway-cost (orchestrator cost watchdog), and prompt-injection (`prompt_guard`, recreated at the input boundary). The one Claude hook without a Grok equivalent is the **stop-guard** (it enforces that an agent calls a terminal MCP verb before a run ends), because the grok CLI's session-stop events are observe-only. This is a workflow-completion guard, not a safety control: a Grok agent that ends without a terminal verb is recovered by the orchestrator reaper / idle watchdog, not left in a dangerous state.

## What this means for routing

Grok is safe to route any agent to, **including the delivery roles** that ingest cross-agent / external content: the prompt-injection guard rejects a poisoned A2A message or task prompt before the model sees it (interactive turns in the driver, the one-shot task prompt in the entrypoint), and the secret-exfiltration guard blocks credential reads / internal-host calls. The only behavioural difference from a Claude-Code-runtime provider is the stop-guard noted above. So security parity is effectively reached; the remaining difference is non-security.
