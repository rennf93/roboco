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
| `system` | Internal orchestrator |

## Teams

| Team | Agents |
|------|--------|
| `backend` | be-pm, be-dev-*, be-qa, be-doc |
| `frontend` | fe-pm, fe-dev-*, fe-qa, fe-doc |
| `ux_ui` | ux-pm, ux-dev-*, ux-qa, ux-doc |
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

## Agent-Specific Fields

| Field | Description |
|-------|-------------|
| `current_task_id` | Currently assigned task |
| `journal_id` | Personal journal |
| `system_prompt` | Base prompt |
| `permissions` | Channel access |
| `metrics` | Performance data |
