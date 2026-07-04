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

The **provider** selects the agent backend, resolved through the `ProviderRegistry` (`roboco/llm/providers/`). `ModelProvider` is `ANTHROPIC` (default — Claude Code), `GROK` (xAI's official `grok` CLI, model `grok-build`, on a SuperGrok subscription), `LOCAL`, `OLLAMA_CLOUD`, or `OPENAI` (reserved). An agent with no dedicated provider falls back to the built-in Claude Code spawn. Grok auth is the host `~/.grok` subscription mount (auto-refreshed by the orchestrator), not a metered API key.

## Agent-Specific Fields

| Field | Description |
|-------|-------------|
| `current_task_id` | Currently assigned task |
| `journal_id` | Personal journal |
| `system_prompt` | Base prompt |
| `permissions` | Tool/verb permission scope |
| `metrics` | Performance data |
