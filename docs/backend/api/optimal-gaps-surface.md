# Route + schema + MCP surface for partial-result gaps

`/kb/search` and `/rag/query` (`roboco/api/routes/optimal.py`) fan a search out across every registered index concurrently. When some indexes finish and others time out or fail outright, the routes now return **HTTP 200 with the completed results plus a `gaps` field** naming the indexes that did not return — whether they timed out or failed with an exception / failure outcome — instead of discarding every result behind a single 504. A hard **504 error envelope** is returned only when every index did not return (a total outage, timeout or failure alike). The MCP tools (`roboco_kb_search`, `roboco_rag_query` in `roboco/mcp/optimal_server.py`) surface the same `gaps` list to the agent rather than dropping it.

The service contract that produces the gaps — `OptimalService.search_with_gaps()` returning `(results, gaps)` and `RAGResponse.gaps` — is documented in [`docs/backend/services/optimal-per-index-timeout.md`](../services/optimal-per-index-timeout.md). This doc covers only the route handlers, response schemas, and MCP tool surface.

## Response schemas

Both `SearchResponse` and `RAGQueryResponse` (`roboco/api/schemas/optimal.py`) gained a dedicated `gaps` field:

```python
gaps: list[str] = Field(
    default_factory=list,
    description="Indexes that did not return — timed out or failed "
    "(empty when all completed)",
)
```

The field is a list of strings, each naming an index that did not return. There are two entry shapes, so a caller can distinguish why an index is missing:

- **Timeout** — `"journals unavailable: timed out after 15s"`.
- **Failure** — `"errors unavailable: failed: RuntimeError"` (a raised non-timeout exception, keyed by exception type) or `"standards unavailable: failed: vector store unreachable"` (a plugin that returned a failure `SearchOutcome`, keyed by its error message).

It defaults to an empty list, so a caller that never reads it is unaffected and every existing client keeps working. A non-empty `gaps` is the signal that the results may be thin because an index did not return — not because the query matched nothing.

## Route handler behavior

### `/kb/search` — partial success vs total outage

The handler calls `service.search_with_gaps(query, context, top_k)` when the method is available (the service layer, Unit A, provides it). It falls back to `service.search(...)` with an empty `gaps` list for backwards compatibility, so a service object that does not yet expose `search_with_gaps` keeps working unchanged.

After the call:

- **Partial success** (some results returned, some gaps) → HTTP 200 with the completed results and `gaps` naming the indexes that did not return (timed out or failed).
- **Total outage** (no results AND gaps non-empty) → HTTP 504 `Gateway Timeout`, detail `"All search indexes failed or timed out: {gaps}"`. Partial degradation never masks a total outage — and because the service layer now populates `gaps` for non-timeout failures too, a total outage where every index *fails* (rather than times out) raises the same 504 instead of slipping through as a misleading HTTP 200 `total: 0`.
- **No results, no gaps** → HTTP 200 with `total: 0`, `gaps: []`. A search that completed but matched nothing is not an outage.

### `/rag/query` — partial success vs total outage

The handler reads `gaps = getattr(response, "gaps", [])` off the `RAGResponse` object (the service layer populates it). The same three cases:

- **Partial success** (citations returned, some gaps) → HTTP 200 with the answer, citations, and `gaps`.
- **Total outage** (no citations AND gaps non-empty) → HTTP 504, detail `"All RAG indexes failed or timed out: {gaps}"` — same total-failure parity as `/kb/search`.
- **No citations, no gaps** → HTTP 200. A query that completed but found no context is not an outage.

The outage guard is `not results and gaps` (search) / `not citations and gaps` (rag) — an empty result set with no gaps is a legitimate "nothing matched" response, not a failure.

## MCP tool surface

`roboco_kb_search` and `roboco_rag_query` (`roboco/mcp/optimal_server.py`) both extract `gaps` from the API response JSON and, when non-empty, include it in the tool response alongside a hint:

```
Partial results: N index(es) timed out (name1, name2). Results may be incomplete.
```

The hint's static wording still says "timed out" for historical reasons (the MCP surface was deliberately not touched by the failure-surface revision), but the parenthesised gap entries themselves spell out the actual cause — a failure entry reads `"nameN unavailable: failed: <detail>"`. The hint for `roboco_rag_query` says "Answer may be incomplete" instead of "Results may be incomplete". When there are no gaps, the `gaps` key and the partial-results hint are omitted entirely — the response is identical to the pre-change shape, so an agent that does not check for `gaps` sees no noise.

The pre-existing "No results" / "Limited results" hints (suggesting `roboco_ask_mentor`) are suppressed when gaps are present, so the agent gets the partial-results hint instead of a misleading "try mentor" nudge on a response that did return partial data.

## Backwards compatibility

- `gaps` defaults to `[]` on both schemas — existing callers deserializing the response are unaffected.
- The route's `getattr` fallback for `search_with_gaps` means a service object that only exposes `search()` still works; the route returns `gaps: []` in that case.
- The MCP tools read `gaps` with `result.get("gaps", [])` — an API response without the field (older server) yields an empty list and no hint.
- `ask_mentor` is explicitly out of scope: its routes, `MentorService`, and MCP tool are untouched.