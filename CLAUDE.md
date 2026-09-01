# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. It keeps only what a session cannot derive from the code. Subsystem doctrine lives in `.claude/rules/*.md` and auto-loads when you touch the matching paths (index at the end).

## Licensing

RoboCo is licensed under **AGPL-3.0** (see `LICENSE`). Copyright (c) 2026 Renzo Franceschini. Do NOT reintroduce an MIT or other license reference anywhere (README, headers, package metadata). The project is AGPL.

Contributions require a signed **Contributor License Agreement** (`CLA.md`), automated via the CLA Assistant workflow (`.github/workflows/cla.yml`). The CLA preserves the option to dual-license / offer a commercial edition later; keep copyright assignment language intact. See `CONTRIBUTING.md`.

## Project Overview

**RoboCo** is an AI Agentic Company - a virtual organization of 25 AI agents + 1 human CEO, designed to operate as a complete software development workforce. The system implements a structured organizational hierarchy with formal communication protocols, task management, and quality controls.

```
CEO (Renzo - Human)
    |
    +-- Intake (on-demand interviewer: chats only with the CEO to draft a task)
    +-- Secretary (on-demand chief-of-staff: reads company state, runs gated CEO directives)
    +-- PR Reviewer (read-only: inbound external/fork + internal PRs, and the root->master in-path gate)
    |
    +-- Board (3 agents)
         +-- Product Owner
         +-- Head of Marketing
         +-- Auditor (silent observer, reports to CEO)
              |
              +-- Main PM (coordinates all cells)
                   |
                   +-- Backend Cell (6 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer)
                   +-- Frontend Cell (6 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer)
                   +-- UX/UI Cell (6 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer)
```

Each agent works in its own git clone under `{ROBOCO_WORKSPACES_ROOT}/{project-slug}/{team}/{agent-slug}` (defaults and auto-clone settings in `roboco/config.py`). The Next.js control panel lives at `roboco/panel/` inside this repo. Workspace and work-session gotchas: `.claude/rules/workspaces-and-git.md`.

## Git Workflow

### Branch Naming Convention

Branch names follow the pattern: `{type}/{team}/{task-hierarchy}`

**Types:** `feature`, `bug`, `chore`, `docs`, `hotfix`

**Task Hierarchy:** Uses `--` separator (not `/`) to avoid git ref conflicts.

**Examples:**
- Root task: `feature/backend/ABC12345`
- Subtask: `feature/backend/ABC12345--DEF67890`
- Sub-subtask: `feature/backend/ABC12345--DEF67890--GHI11111`

### Commit Format

Commits are automatically prefixed with the task ID:

```
[{task-id[:8]}] {message}
```

**Example:**
```
[ABC12345] Add user authentication endpoint
```

### Git credentials and forges

Git authentication is per-project: each project stores its own Fernet-encrypted GitHub PAT, there is no global fallback, and the API only ever returns `has_git_token`. **HTTPS URLs require tokens**: cloning without one raises `WorkspaceError`. GitHub, Gitea, and GitLab are all supported through the provider-routed forge layer (`roboco/services/forge/`); `projects.protected_branches` only ever tightens the hardcoded `{master, main}` safety floor. Details: `.claude/rules/workspaces-and-git.md`.

## Task Lifecycle

The lifecycle is defined in `roboco/foundation/policy/lifecycle.py` (`roboco/enforcement/task_lifecycle.py` is a backwards-compat shim over it). The non-obvious rules:

- **PR before QA.** A developer opens the PR before `awaiting_qa` so QA and every later reviewer work off a real diff. Assembled cell->root and root->master PRs pass the in-path PR-review gate (`awaiting_pr_review`) before the PM merges; a zero-commit assembled branch waives the PR (`pr_waived` marker) instead of failing on GitHub's "No commits between".
- **Only the CEO merges master.** A root PR ends at `awaiting_ceo_approval`; the CEO approves (merge), requests changes, or cancels. Board roles never own a coordination root.
- **Sequence is the bar.** A task cannot be claimed while a same-parent sibling with a strictly lower effective sequence is non-terminal (reachability-aware outside MegaTask batches). Cancelled siblings never block.
- **Bounces are structured.** QA `fail_review`, `pr_fail`, PM `request_changes`, and `ceo_reject` write findings to `task_review_findings`; every open finding must be named in `resolved_findings` before `i_am_done` / `submit_up` / `submit_root` is accepted. Blocker/major findings are never waivable.
- **Everything is a task.** No work without a task, no task without acceptance criteria, no closure without documentation, every commit references its task ID, state must survive interruption.

Full doctrine (PR gate, waiver, auto-submit, findings ledger): `.claude/rules/task-lifecycle.md`.

## Communication Model

Agents coordinate through task state and task detail fields. Two primitives sit alongside that: **A2A** (`dm` / `read_a2a`, same-cell peer contact) and **Notifications** (`notify`, ack-required, PMs/Board only). The CEO is asymmetric: it can open a DM with any DM-capable agent from the panel, but no agent ever initiates to the CEO. Unacked notifications re-escalate on an exponential backoff, never every sweep tick. Details: `.claude/rules/notifications-and-a2a.md`.

**Shared-session DB discipline (applies everywhere).** Wrap every best-effort write on a shared, reused session in `session.begin_nested()`; a bare `except Exception: log(...)` leaves the session poisoned. A swallowed savepoint rollback also EXPIRES every attribute of any ORM object mutated inside the block, so the except path must `await session.refresh(obj)` before touching it again, or the next read raises `MissingGreenlet` and rolls back the whole request.

## Agent Gateway

Agents never call the API or per-domain MCP tools directly. They go through two thin MCP servers (`roboco-flow` for intent verbs, `roboco-do` for content tools) backed by the server-side **Choreographer** in `roboco/services/gateway/`, which composes the services into verb sequences and centralizes tracing, claim-locking, evidence, and remediation hints. The per-role verb surface comes from `lifecycle.intents_for_role` and `roboco/services/gateway/role_config.py`, mounted into each container as `/app/tool-manifest.json`. Every verb returns an **Envelope**: on success `{status, task_id, next, evidence?, context_briefing}`, on error `{error, message, remediate, missing}`. Agents trust `next` and `remediate` rather than guessing state. Guard doctrine: `.claude/rules/gateway-guards.md`.

## Agent Providers

Backends are pluggable (`roboco/llm/providers/`, `ProviderRegistry` keyed by `ModelProvider`): Claude Code (default), Grok, Gemini, Codex, Kimi. Routing resolves `(provider, model)` per agent at spawn with precedence `AGENT_SLUG > ROLE:complexity > ROLE > GLOBAL`, and a capability floor upgrades any below-floor Anthropic assignment to Sonnet because Haiku cannot emit the structured envelopes. Per-runtime auth and tool-scoping gotchas: `.claude/rules/agent-providers.md`.

## Feature Flags

Default-off subsystems are env-gated `ROBOCO_*_ENABLED` flags declared in `roboco/config.py` and toggled from the panel's Settings -> Feature Flags card (persisted in the settings store, effective on the next backend restart). Two flags are deliberately NOT on that card because they must travel with the compose files: `ROBOCO_CLOUD_AUTH_ENABLED` and `ROBOCO_DB_NETWORK_ISOLATED`. Board Programs arm per-program on their own page (`board_program.{key}.enabled` settings rows), not on the card. Every autonomy engine originates ONE held task or artifact and never auto-merges, auto-posts, or auto-deploys; the CEO is the only path to materialization.

## Architectural Conventions Standard

Each project can carry `.roboco/conventions.yml` (which definition kinds live in which modules, rules, waivers); RoboCo ships its own. When armed (`ROBOCO_CONVENTIONS_ENABLED`), a `block`-level finding from `python -m roboco.conventions check --root <repo> --files ...` refuses `i_am_done` and `pr_pass`; a false positive is relieved by a waiver committed in the branch, never by `# noqa` / `# type: ignore`. Details: `.claude/rules/conventions-standard.md`.

## MegaTask

A MegaTask is a branchless **umbrella** task over N **root-subtasks** (each a real Main-PM coordination root with its own project, branch, and PR), sequenced into collision-aware waves by the pure `SequencingService`. Identity predicates live in `roboco/foundation/policy/batch.py` and are the single source of truth for every git exemption. Details: `.claude/rules/megatask.md`.

## Deployment

- `nginx` (`localhost:3000`) is the only exposed service: `/api/*` and `/ws/*` go to `orchestrator:8000`, everything else to `panel:3000`. The panel uses relative `/api` and `/ws` URLs; never add a second origin.
- Two bridges: `roboco_default` (panel, nginx, ollama, agents, sandboxes) and `roboco_data` (postgres + redis ONLY). The orchestrator is the only multi-homed service, so agent containers cannot reach the DB by construction; DB-needing projects use the sandbox opt-in. `ROBOCO_DB_NETWORK_ISOLATED=true` rides the compose files that carry this topology.
- Schema changes ship only as Alembic migrations under `alembic/versions/`; after pulling one, run `docker compose exec orchestrator alembic upgrade head`.
- `make quickstart` is the pull-and-run bring-up (`scripts/bootstrap.sh`); the release workflow's `pull-smoke` job pulls the registry compose against every published tag.

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `404 /api/embed` | Model not pulled | Check `docker logs roboco-ollama-init` |
| `All connection attempts failed` | API not ready | Orchestrator starts before FastAPI lifespan completes |
| Healthcheck failing | Wrong endpoint | Use `ollama list` not `curl` |

## Subsystem doctrine (`.claude/rules/`, auto-loaded by path)

- `workspaces-and-git.md`: clone reset on fresh claim, `uv sync --extra dev`, stale-state fixes, forge adapters, protected-branch fail-open vs fail-closed
- `task-lifecycle.md`: PR gate, zero-diff waiver, sequence bar, PM auto-submit, revision findings ledger
- `gateway-guards.md`: possibilities matrix, collision map, delegation fidelity, budgets, PR labels, rate-limit parking, tool-call budget, spawn-waste metrics
- `notifications-and-a2a.md`: re-escalation backoff, outbox + savepoint interaction
- `agent-providers.md`: routing presets, Grok/Gemini/Codex/Kimi auth and tool-scoping
- `autonomy-engines.md`: self-heal, CI-watch, dep-update, docs-sync, release manager, env-branch ladder
- `board-programs-and-x.md`: the fourteen Board Programs, X account posting rules
- `sandbox.md`, `cloud-auth.md`, `conventions-standard.md`, `megatask.md`, `api-streams.md`, `doctrine-memory-eval.md`
- `video-engine.md`, `obsidian-vault.md`, `telegram-bridge.md` (pre-existing)

## Blueprint Reference

User-facing documentation is the docs site at **[docs.roboco.tech](https://docs.roboco.tech)** (the `roboco-website` repo). In this repo, `docs/rag/` is the agent-facing RAG corpus and `docs/map/` the agent-facing codebase map, both auto-indexed into the KB at startup (`OptimalService.AUTO_INDEX_DIRS`); `docs/internal/` holds specs and working notes; `docs-redirects/` keeps the old Pages URLs resolving.
