# LLM provider security posture

RoboCo routes each agent to one of several LLM providers (the **Routing** card in the control panel). This note states — truthfully — what protections an agent gets on each provider, so the choice is made knowingly. The short version: **Grok agents do not have full guardrail parity with the rest, but they are still usable.** Use them for trusted work; prefer a Claude-Code-runtime provider for agents that ingest untrusted or cross-agent content.

## Two runtimes, not five

An agent has two layers: the **model** (the brain) and the **runtime** (the agent program that drives it — reads files, calls tools, loops). RoboCo's guardrails are implemented as **Claude Code hooks**, so they only exist when the runtime is Claude Code.

| Provider (routing mode) | Runtime | Model |
|---|---|---|
| Anthropic | Claude Code | Claude (opus/sonnet) |
| Ollama (Cloud) | Claude Code (via `ANTHROPIC_BASE_URL` injection) | the Ollama model |
| Self-Hosted | Claude Code (via `ANTHROPIC_BASE_URL` injection) | your endpoint's model |
| **Grok (xAI)** | **opencode** | **grok-build-0.1** |

Anthropic, Ollama, and Self-Hosted all run on **Claude Code** and therefore keep the **full guard set**. Only **Grok** runs on a different runtime — **opencode** — because the `claude` binary rejects any non-Claude model id, so Grok cannot run inside Claude Code. opencode is to Grok what Claude Code is to Claude.

## Guardrail parity matrix

| Guardrail | Claude Code runtime (Anthropic / Ollama / Self-Hosted) | Grok (opencode) |
|---|---|---|
| MCP gateway + role tool-manifest | yes | yes (mounted by construction) |
| Command / secret-exfiltration guard (bash, credential files, internal-host calls, PAT exfil) | yes (`bash-guard-hook.sh`, PreToolUse) | **yes** — ported to opencode as the `secret-scrub.js` plugin (`tool.execute.before`) |
| Budget / runaway-cost kill-switch | yes (`post-tool-budget` hook against the SDK server) | **yes** — orchestrator-side cost watchdog (`ROBOCO_GROK_MAX_COST_USD`) reading the opencode store |
| Prompt-injection guard (rejects "ignore previous instructions", role-override, fake escalations in incoming A2A / task / notification content) | yes (`user-prompt-hook.sh`, UserPromptSubmit, denies the turn) | **no** — opencode's incoming-message hook (`message.updated`) is observe-only and cannot block a turn before the model reads it |
| Stop-guard (terminal-verb enforcement before a run ends) | yes (`stop-hook.sh`, Stop) | **no** — opencode's stop/idle hooks are observe-only |

## Why the two gaps exist (not a defer — a runtime limitation)

opencode's plugin API exposes only observe-only events for incoming messages and session-stop, and **no token/usage hook at all**. So the prompt-injection and stop-guard hooks — both of which must *block* an action — have no faithful opencode equivalent today. Closing them would require an upstream opencode feature (a blocking message/stop hook). The budget guard was movable to the orchestrator (it reads the opencode cost store), which is why Grok keeps budget parity but not injection/stop parity.

## What this means for routing

- **Grok is safe for trusted, self-contained work** — and for the interactive intake/secretary roles, whose input comes directly from the CEO (a small injection surface).
- **The real injection exposure is the delivery roles** (developer / qa / pm / documenter), which routinely ingest *other agents'* and external content as data. The prompt-injection guard is what stops a poisoned A2A message or task description from steering them off-task; on Grok that guard is absent.
- **Recommendation:** route delivery agents that handle untrusted or cross-agent content to a Claude-Code-runtime provider (Anthropic / Ollama / Self-Hosted). Route Grok where the content is trusted, or accept the reduced posture knowingly. The command/secret-exfiltration guard — the one that prevents actual credential leakage — *is* present on Grok, so the gap is about being socially-engineered off-task, not about secret exfiltration.

This is the honest claim: **not full parity, still usable.** The control panel's Routing card surfaces a short version of this when Grok or Mix mode is selected.
