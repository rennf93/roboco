# Shipped-work digest: shared helper + board-program prompt injection

The shipped-work digest is a server-assembled "what the fleet shipped this week" context block injected into Product Owner exploration prompts so the PO does not re-propose work that already shipped. It combines recently completed task titles (capped at 15) with the CHANGELOG.md `## [Unreleased]` body, degrades explicitly when either half is unavailable, and is shared across four Board Program prompts via one helper in `roboco/utils/`.

## The shared helper — `roboco/utils/shipped_work_digest.py`

`shipped_work_digest(session, roboco_project_slug) -> str` is an async, side-effect-free helper that assembles both halves of the digest:

- **Completed this week** — queries `TaskTable` for `COMPLETED` tasks with `completed_at` in the last 7 days, outer-joined to `ProjectTable` for the project name, ordered by `completed_at` descending, capped at `_DIGEST_TASK_LIMIT = 15`. Each row renders as `- {title} ({project_name}, {team})`. An empty set degrades to `- (nothing completed)`.
- **CHANGELOG Unreleased section** — reads the RoboCo project's read clone via `get_workspace_service(session).ensure_read_clone(slug)`, then calls `_read_changelog` and `_unreleased_body` from `roboco/services/release_readiness.py` to extract the `## [Unreleased]` body. A read failure (missing file, missing section, clone error) degrades to `(not available this cycle)`. The import of `get_workspace_service` is lazy (inside the function body, `noqa: PLC0415`) so `roboco/utils/` stays clear of service-module imports at import time, per the architectural standard.

The helper never raises and never returns an empty string — both halves always carry an explicit line so the rendered section is never a bare header.

The helper is re-exported from `roboco/utils/__init__.py`.

## Megaphone refactor — delegation

`MegaphoneEngine.digest_context()` (`roboco/services/megaphone_engine.py:126`) previously contained the digest assembly logic inline (`_shipped_this_week`, `_unreleased_changelog`, and the `digest_context` orchestration). It now delegates to the shared helper:

```python
async def digest_context(self) -> str:
    slug = (settings.self_heal_project_slug or "roboco-api").strip()
    return await shipped_work_digest(self.session, slug)
```

Megaphone's existing behavior is unchanged — the regression test (`test_regression_digest_output_format_unchanged`) pins the exact output format. The orchestrator's existing `_megaphone_digest_context()` wrapper (which calls `get_megaphone_engine(db).digest_context()`) is untouched and retains its best-effort degrade-to-empty-string posture.

## Orchestrator injection — roadmap, Pest Control, Spackle

The orchestrator mirrors the megaphone pattern for all three Product Owner exploration prompts. Each has a dispatch method that gathers context and a prompt-builder method that renders it.

### `_shipped_work_digest_context()` wrapper

`AgentOrchestrator._shipped_work_digest_context()` (`orchestrator.py:14843`) is the best-effort wrapper that calls the shared helper directly (not through `MegaphoneEngine`) so the PO prompts do not depend on the megaphone engine. It catches all exceptions, logs a warning, and returns `""` on failure — a digest failure drops the section from the prompt, it never blocks a spawn.

### Dispatch wiring

Each dispatch method calls `_shipped_work_digest_context()` and passes the result to its prompt builder:

| Dispatch method | Prompt builder | Line (dispatch) |
|---|---|---|
| `_dispatch_roadmap_exploration` | `_build_roadmap_prompt(task, prior_context, market_brief_context, digest_context)` | 14325 |
| `_dispatch_pest_control_exploration` | `_build_pest_control_prompt(task, prior_context, evidence_context, digest_context)` | 14380 |
| `_dispatch_spackle_exploration` | `_build_spackle_prompt(task, prior_context, digest_context)` | 14562 |

### `_shipped_digest_block()` renderer

The module-level helper `_shipped_digest_block(digest_context)` (`orchestrator.py:1190`) renders the `## Shipped-this-week digest` block for all three prompts. It returns `""` when no digest was assembled so the section is omitted entirely (no empty header, no orphan instruction). When a digest is present, it renders:

```
## Shipped-this-week digest
{digest_context}

{instruction text}
```

### Instruction text

`_SHIPPED_DIGEST_INSTRUCTION` (`orchestrator.py:1182`) carries the "do not propose already-shipped work" directive, adapted from the intake prompter's existing "don't propose what's already been done" wording (`agents/prompts/roles/prompter.md`):

> Before proposing, check the shipped-this-week digest above — do not propose already-shipped work. If a candidate item duplicates work that already shipped this week (named above) or is in flight, say so plainly and skip it instead of quietly drafting a duplicate.

## Degradation path

The degradation has three layers, each explicit:

1. **Empty completed set** — the helper renders `- (nothing completed)` rather than omitting the section.
2. **Changelog read failure** — the helper renders `(not available this cycle)` rather than omitting the section or raising.
3. **Orchestrator wrapper failure** — `_shipped_work_digest_context()` returns `""`, so `_shipped_digest_block("")` returns `""` and the entire digest section (header + instruction) is omitted from the prompt. The spawn proceeds normally.

A digest failure never blocks a cycle. The prompt either carries the digest with instruction text, or omits the section entirely — never an empty header or a failed spawn.

## Test coverage

Two test files, nine tests total:

- `tests/unit/test_shipped_work_digest.py` — regression (pins the exact output format), degradation (empty shipped + missing changelog, changelog exception does not raise).
- `tests/unit/runtime/test_board_program_shipped_digest_prompts.py` — digest block rendering (present + empty), digest presence in each of the three prompts (roadmap, Pest Control, Spackle), instruction text presence, and omission when no digest is available.