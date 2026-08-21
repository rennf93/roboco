# OptimalService: per-index timeout + partial-result gaps

`OptimalService` (`roboco/services/optimal.py`) fans a search out across every
registered index plugin concurrently. Before this change the **whole** fan-out
sat inside one route-level `asyncio.timeout(30.0)` in `/kb/search` and `/rag/query`
(`roboco/api/routes/optimal.py`); on expiry the route returned a `504` with zero
results, discarding every index that had already finished. The 15s per-index
bound that lived in `optimal_brain/indexes/base.py` `ask()` was dead code on this
path — neither route called it.

This change adds a real **per-index** timeout, inner to the route's 30s outer
bound, and returns partial results plus a visible `gaps` list naming the indexes
that did not finish. It follows the bounded-leg + visible-gaps-list degradation
pattern already used by `roboco/services/gateway/choreographer/evidence_legs.py`
(`run_bounded_leg`) — no new pattern was invented.

## The bound

```python
_PER_INDEX_SEARCH_TIMEOUT = 15.0
```

Each index plugin's `search_with_embedding()` call is wrapped in
`asyncio.wait_for(..., timeout=_PER_INDEX_SEARCH_TIMEOUT)` independently, so one
slow index cannot consume the whole request budget. On `TimeoutError` the index
is recorded in a `gaps` list and a failure `SearchOutcome` is returned instead of
propagating — the other indexes still return their results. The route-level 30s
`asyncio.timeout` stays as the outer bound; the per-index 15s bound is inner.

## Public surface

### `search_with_gaps()` — the new method

```python
async def search_with_gaps(
    self, query, context=None, top_k=5
) -> tuple[list[SearchResult], list[str]]
```

Returns `(results, gaps)` where `gaps` is a list of strings, each naming an
index that timed out (e.g. `"journals unavailable: timed out after 15s"`).
Empty when every index completed. This is the method the in-scope routes
(`/kb/search`, `/rag/query`) and the MCP tool surface should call when they want
to surface partial-result degradation to the caller.

### `search()` — backward compatible

`search()` delegates to `search_with_gaps()` and discards the `gaps` list,
returning `list[SearchResult]` exactly as before. Every existing caller is
unchanged:

- `MentorService` / `ask_mentor` (`roboco/services/mentor.py`) — intentionally
  out of scope. It does not share the route-level wrapper and
  `MentorService._search_all_indexes` already tolerates partial failures with
  `search_stats` / `search_errors`, the opposite failure mode. It was left
  untouched.
- `LearningService`, `JournalService`, `EvidenceRepoService`,
  `ProactiveContextService` — all call `search()` and are unchanged.

### `query()` and `RAGResponse.gaps`

`_aggregate_citations()` now returns a 4-tuple
`(citations, stats, errors, gaps)`; `_search_single_index()` accepts an
optional `gaps` param and, on timeout, appends the index name to it and records
an error in the buffer instead of propagating. `query()` threads `gaps` into
the returned `RAGResponse`:

```python
@dataclass
class RAGResponse:
    answer: str
    citations: list[SearchResult]
    query: str
    context_used: int
    search_stats: dict[str, int] = field(default_factory=dict)
    search_errors: dict[str, str] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)  # Indexes that timed out
```

`gaps` defaults to an empty list, so any caller constructing or reading a
`RAGResponse` that does not set it keeps working unchanged.

## Caller audit

All callers of `OptimalService.search` / `search_with_embedding` /
`_aggregate_citations` were audited before the signature changed:

- `search()` keeps its `list[SearchResult]` return — every external caller is
  unaffected.
- `_aggregate_citations` is internal to `OptimalService` (only called from
  `query()`), so its new 4-tuple return had exactly one call site to update.
- `_search_single_index`'s new `gaps` param is optional (`list[str] | None`).
- `RAGResponse.gaps` defaults to `[]`.

## Route / MCP wiring (separate task)

Surfacing `gaps` in the `/kb/search` and `/rag/query` response schemas and the
MCP tool is the work of the parallel route/schema/MCP task (Unit B). The service
contract — `search_with_gaps()` returning `(results, gaps)` and `RAGResponse`
carrying `gaps` — is what this task ships; routes still call `search()` and
return their existing schemas until Unit B lands.

## Degradation pattern reference

The pattern mirrors `evidence_legs.run_bounded_leg`: a bounded leg, and on
timeout the failing unit is named in a visible gaps list while the successful
units still return their results. `_bounded_index_search()` is the module-level
helper that wraps a single `(IndexType, plugin)` entry's search in
`asyncio.wait_for` and, on `TimeoutError`, appends a human-readable string to
the shared `gaps` list and returns a failure `SearchOutcome`. Reuse this
helper rather than adding another per-call timeout when extending the fan-out.