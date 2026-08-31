# KB Provenance Flip: live_write → repo_tree on Root-Chain Completion

Gives the `live_write` provenance marker a lifecycle. A doc a task writes mid-task via `roboco_docs_write` is indexed with `provenance: "live_write"` + the writing task's id in chunk metadata, and every KB search hit built from it appends an in-flight-work caveat. Before this feature that caveat was permanent — no hook ever flipped provenance back, so a merged-and-shipped doc kept telling readers "verify against git" forever. Now the flip happens automatically: when the writing task's ROOT chain reaches terminal `completed`, every chunk stamped with a task id in that root's subtree gets its provenance updated to `repo_tree` in place, and the caveat disappears from all subsequent `roboco_kb_search` / `roboco_rag_query` / `roboco_ask_mentor` hits.

## Why a completion trigger, not a merge trigger

In this system the PR merge precedes task completion (the `complete` / `ceo_approve` paths merge the PR, then transition the task), so a completion hook subsumes a PR-merge listener — no second listener needed. Both completion funnels (`TaskService.complete` and the CEO-approve path) already route through `_trigger_completion_hooks` post-flush, so one hook covers both seams.

## The root-chain guard

The safety semantic: a leaf or cell task completing while its root ancestor is still in flight must NOT flip — the caveat stays until the whole root ships to master. `TaskService._completed_root_subtree_ids` (`roboco/services/task.py`) implements the guard with two recursive CTEs modeled on `resolve_root_source`:

1. Walk `parent_task_id` up to the root ancestor. If the root's status is anything other than `completed`, return `None` — no flip.
2. Otherwise walk the subtree down from the root and return every task id in it, sorted (the recursive CTE's row order is unspecified; sorting keeps the returned list deterministic for tests and log lines).

A parentless task is its own root, so completing it completes the chain and flips its own docs. Depth caps (`MAX_TASK_DEPTH + 4`) are safety nets against a corrupt cycle only — they can never truncate a real chain.

## The flip pipeline

Each layer is narrow and adds exactly one concern:

| Layer | Location | What it does |
|---|---|---|
| Store | `VectorStore.flip_provenance` (`roboco/services/optimal_brain/vector_store.py`) | ONE targeted `UPDATE ... SET metadata = jsonb_set(metadata, '{provenance}', ...)` over the docs chunks table, `WHERE metadata->>'task_id' = ANY(ids) AND metadata->>'provenance' = from_value`, `RETURNING id` to count. No delete + re-embed, no reindex, no repo-tree re-derivation — unchanged docs are structurally out of scope. |
| Plugin | `DocsIndexPlugin.flip_task_provenance` (`roboco/services/optimal_brain/indexes/docs.py`) | Exposes the store flip at plugin level; empty id list short-circuits to 0 without touching the store. |
| Service | `OptimalService.flip_docs_task_provenance` (`roboco/services/optimal.py`) | Resolves the docs plugin; a non-`DocsIndexPlugin` registration logs a debug line and returns 0. |
| Trigger | `TaskService._flip_docs_provenance_background` (`roboco/services/task.py`) | Lazily resolves `get_optimal_service()` and flips with the completing root's whole subtree ids. |

Both from/to provenance values are parameters (`live_write` → `repo_tree` is the call the trigger makes), so the same path can flip between any two provenance values.

## The trigger in `_trigger_completion_hooks`

The guard resolves **inline, on the same session that just flushed the status change** — it must see the completing transition; a background resolution would race it. The KB flip itself runs as a fire-and-forget `asyncio` background task registered on `TaskService._background_tasks`, so a KB outage can never delay or fail a task completion. Failure policy is best-effort at both layers, mirroring `DocsService._index_doc_in_rag`:

- The guard CTE failing → structlog warning ("Docs provenance-flip guard failed"), completion hooks continue.
- The flip itself failing (store down, plugin missing, import error) → structlog warning ("Failed to flip docs provenance on completion"), nothing propagates.

## What the flip does NOT do

- No reindex, no re-embedding, no content rewrite — chunk vectors are untouched; only the `provenance` metadata key moves.
- `AUTO_INDEX_DIRS` (`docs/rag`, `docs/map`) is unchanged. Widening it to the team dirs would re-index unchanged docs and still couldn't fix a doc whose file was already deleted from the tree — a targeted metadata update by the stamped `task_id` reaches exactly the right chunks.
- The caveat-append logic in `roboco/mcp/optimal_server.py` is untouched — it stays purely provenance-gated, so flipping provenance removes the caveat automatically. Only its lifecycle comment was refreshed.
- Docs the periodic scan re-indexes from the repo tree already carry `repo_tree` and never participate in the flip (`WHERE provenance = 'live_write'`).

## Timing semantics for readers

Between "task completed" and "flip ran" there is a small async window (the flip runs in the background). In the common case the caveat clears within seconds of the root's completion. If the flip permanently failed (KB down for the whole window), the chunk keeps `live_write` until the doc is re-indexed from the repo tree by an operator reindex — the old pre-hook behavior. That residual case is why a caveat still means "verify against git", never a hard "this is unmerged".

## Tests

- `tests/unit/services/optimal_brain/test_docs_index_provenance.py` — store SQL/params for the flip, empty-ids no-op, plugin delegation.
- `tests/integration/test_task_provenance_flip.py` — the trigger: flip fires with the root's whole sorted subtree ids; no flip while the root is non-terminal; a parentless task flips itself; flip failures swallowed + logged; guard failures never break completion hooks.
- `tests/integration/test_docs_service.py::test_doc_flips_to_repo_tree_when_root_chain_completes` (beside the existing `live_write` provenance tests) — the end-to-end doc-written-then-chain-completes lifecycle.