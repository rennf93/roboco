# Agent Model

## Core Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `name` | String | Display name |
| `slug` | String | URL-safe ID (e.g., `be-dev-1`) |
| `role` | Enum | Agent role |
| `team` | Enum | Team affiliation |
| `status` | Enum | active, idle, offline |

## Roles

| Role | Description |
|------|-------------|
| `ceo` | Human executive |
| `product_owner` | Product strategy |
| `head_marketing` | External comms |
| `auditor` | Silent observer |
| `main_pm` | Coordinates all cells |
| `cell_pm` | Manages one cell |
| `developer` | Writes code |
| `qa` | Reviews and tests |
| `documenter` | Writes documentation |
| `pr_reviewer` | Read-only reviewer: inbound external/fork + internal PRs, and the in-path assembled-PR gate (`pr-reviewer-1` main + `be/fe/ux-pr-reviewer` per cell) |
| `prompter` | On-demand intake interviewer, human-only (agent `intake-1`) |
| `secretary` | On-demand chief-of-staff, human-only (agent `secretary-1`) |
| `system` | Internal orchestrator |

## Teams

| Team | Agents |
|------|--------|
| `backend` | be-pm, be-dev-*, be-qa, be-doc, be-pr-reviewer |
| `frontend` | fe-pm, fe-dev-*, fe-qa, fe-doc, fe-pr-reviewer |
| `ux_ui` | ux-pm, ux-dev-*, ux-qa, ux-doc, ux-pr-reviewer |
| `main_pm` | main-pm |
| `board` | product-owner, head-marketing, auditor |
| `marketing` | head-marketing |

## Status

| Status | Meaning |
|--------|---------|
| `active` | Currently working |
| `idle` | Available for work |
| `offline` | Not available |

## Capabilities

Example capabilities:
- `code_execution`
- `git_operations`
- `documentation`
- `testing`

## Model Configuration

Stored in `model_config` JSON:
- LLM provider
- Model name
- Temperature
- Other settings

The **provider** selects the agent backend, resolved through the `ProviderRegistry` (`roboco/llm/providers/`). `ModelProvider` is `ANTHROPIC` (default — Claude Code), `GROK` (xAI's official `grok` CLI, model `grok-build`, on a SuperGrok subscription), `GEMINI` (Google's official `gemini` CLI, OAuth login), `OPENAI` (OpenAI's official `codex` CLI, ChatGPT subscription), `KIMI` (Moonshot's official `kimi`/kimi-code CLI, Kimi subscription via OAuth device-code login), `LOCAL`, or `OLLAMA_CLOUD`. An agent with no dedicated provider falls back to the built-in Claude Code spawn. All four run on a mounted host subscription credential rather than a metered API key; Codex, Gemini, and Kimi are one-shot delivery-role runtimes only (no Intake/Secretary), while Grok additionally drives the interactive Intake and Secretary chats. Grok's `~/.grok` is auto-refreshed by the orchestrator; Kimi's `~/.kimi-code` is mounted read-write and shared across every Kimi agent, since Moonshot's refresh token is rotation-with-short-reuse-grace and every container redeems the same rotating chain. Kimi runs headless via `-p` with stream-json output, scopes tools through rendered deny-rules plus a `PreToolUse` bash-guard wrapper hook (no CLI-flag tool-removal equivalent), captures usage by summing `wire.jsonl`'s token buckets, and parks on rate-limit (exit 75) or an expired/missing credential (exit 78) exactly like Grok/Codex/Gemini so the orchestrator can pause and later revive it.

## Agent-Specific Fields

| Field | Description |
|-------|-------------|
| `current_task_id` | Currently assigned task |
| `journal_id` | Personal journal |
| `system_prompt` | Base prompt |
| `permissions` | Tool/verb permission scope |
| `metrics` | Performance data |
