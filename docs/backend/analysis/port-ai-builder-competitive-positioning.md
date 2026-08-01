# Competitive positioning: Port AI Builder vs. RoboCo

**Market signal.** On 2026-07-14 Port launched "Port AI Builder," billed as the industry-first purpose-built vibe coding experience for platform engineering. Its three advertised pillars: human-in-the-loop review and governance, a "Context Lake" for org-specific context, and baked-in domain skills (SRE, DevOps, security, AI governance). Source: https://www.port.io/news/port-ai-builder-announcement

This doc maps each pillar to the concrete RoboCo backend surface that already serves the same purpose, citing the actual implementation, and states whether RoboCo is equivalent, superior, or genuinely behind on each.

## 1. Human-in-the-loop review and governance

**RoboCo surface: the CEO-approval workflow.**

- RoboCo has a shared CEO-check helper, `require_ceo_role()` (`roboco/api/deps.py:627`), which unifies the orchestrator-router and release-handler gates; the task-approval endpoints below enforce the same CEO-only rule inline.
- The lifecycle spec (`roboco/foundation/policy/lifecycle.py:358-369`) encodes `AWAITING_CEO_APPROVAL -> COMPLETED` (`ceo_approve`) and `AWAITING_CEO_APPROVAL -> NEEDS_REVISION` (`ceo_reject`) as `frozenset({Role.CEO})`-only transitions — no other role can execute either.
- `POST /api/tasks/{id}/ceo-approve` (`roboco/api/routes/tasks.py:2171-2224`) enforces the CEO-only check inline and additionally *requires a substantive note* (`_MIN_NOTES_CHARS`, >= 20 chars) recording why the work is approved for production — an audit trail Port's announcement doesn't detail at this granularity.
- `POST /api/tasks/{id}/approve-and-merge` (`roboco/api/routes/tasks.py:2266-2348`) is the merge-to-master step itself: CEO-only, requires an existing PR (`pr_number`), and calls `GitService.merge_pr_for_task` to squash-merge.
- `TaskService.ceo_approve()` (`roboco/services/task.py:7256`) additionally refuses to approve unless the work session's PR is already `merged` — the human sign-off is structurally the last gate before a task can reach `completed`.
- Beneath the CEO gate, every task already passes an automated PR-review gate (`awaiting_pr_review`, a dedicated `pr_reviewer` role) and a QA pass before it ever reaches the CEO — governance is layered, not a single checkbox.

**Verdict: functionally equivalent, arguably superior.** RoboCo's CEO gate is a hard, role-checked, single-source-of-truth state-machine transition with a mandatory audit note and a PR-merged precondition, sitting on top of an independent PR-review + QA layer. Port's write-up describes review/governance as a feature of its builder UI; RoboCo's equivalent is enforced at the state machine and API layer, not just presented in a UI.

## 2. Context Lake for org-specific context

**RoboCo surface: the in-house RAG/knowledge-base system (`OptimalService`).**

- `OptimalService` (`roboco/services/optimal.py:158`) is a plugin-based architecture over PostgreSQL + pgvector with a registry of indexes (`PLUGIN_REGISTRY`, `roboco/services/optimal.py:144-155`) covering documentation, journals, errors, standards, decisions, reviews, learnings, playbooks, and CEO vault notes — i.e. org-specific context accumulated from every agent's actual work, not a generic corpus.
- `OptimalService.search()` (`roboco/services/optimal.py:1230`) embeds a query once and runs every index's hybrid (vector + keyword) search concurrently; `OptimalService.query()` (`roboco/services/optimal.py:1339`) aggregates citations across indexes and synthesizes a single answer.
- These are exposed to every agent as MCP tools: `roboco_kb_search` (`roboco/mcp/optimal_server.py:92`, semantic search) and `roboco_ask_mentor` (`roboco/mcp/optimal_server.py:393`, conversational RAG with follow-up context).
- Retrieval isn't only pull-based: `EvidenceRepo.similar_memory()` (`roboco/services/gateway/evidence_repo.py:465`) proactively injects the top-K relevance-floored institutional-memory hits (distilled learnings, approved playbooks, CEO vault notes) into an agent's `context_briefing` at claim time — an agent gets relevant org context pushed to it before it has to think to search.

**Verdict: functionally equivalent.** RoboCo's RAG stack is the direct analogue of a "Context Lake": org-specific, continuously fed by real agent output (decisions, learnings, journals, reviews), searchable and synthesizable, and additionally push-injected at claim time rather than being pull-only. No gap identified; the branding differs, the capability does not.

## 3. Domain skills (SRE, DevOps, security, AI governance) baked in

**RoboCo surface: per-role/team prompts + the architectural-conventions gate.**

- Every agent's system prompt is composed (`compose_prompt`) from layered role/team prompt files under `agents/prompts/` — e.g. `agents/prompts/roles/developer.md` and the per-team file (`agents/prompts/teams/backend.md`) — which embed the team's tech stack, quality-gate commands, and domain conventions directly into every spawn, not as optional documentation.
- Security/coding/workflow domain guidance is also retrievable on demand via the `roboco_get_standards` MCP tool, backed by `StandardsIndexPlugin` (`roboco/services/optimal_brain/indexes/standards.py`).
- Architectural governance is enforced, not advisory: `.roboco/conventions.yml` (`ROBOCO_CONVENTIONS_ENABLED` - default-off for new projects, on in RoboCo's own deployment, which ships a canonical `.roboco/conventions.yml`) defines which module kinds may hold which definitions; the validator (`roboco/conventions/runner.py:42`) classifies every changed definition and raises `Finding`s. A `block`-level finding (a model in a router, a suppressed lint/type check) hard-refuses both `i_am_done` (`_conventions_gate`, `roboco/services/gateway/choreographer/_impl.py:2498`) and the PR-reviewer's `pr_pass` (`_conventions_guard`, same file, line 2540) — the offending `file:line` plus fix hint is returned in the rejection, and a false positive can only be cleared by committing an explicit, reviewed waiver.

**Verdict: functionally equivalent, arguably superior on enforcement.** Port advertises domain skills as built-in guidance inside its builder; RoboCo's equivalent is both prompt-embedded guidance (present at every spawn, not opt-in) and a deterministic, code-level enforcement gate that blocks submission on a real violation — a stricter guarantee than "skills baked in" implies for Port.

## Overall conclusion

RoboCo has no concrete capability gap versus the Port AI Builder market signal: all three advertised pillars (human-in-the-loop governance, an org-context knowledge base, and baked-in domain-skill enforcement) already exist as real, enforced backend surfaces in RoboCo today, and on two of the three (governance, domain-skill enforcement) RoboCo's mechanism is stricter than what Port's announcement describes — this is a branding/marketing difference, not a functionality difference.
