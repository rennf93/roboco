# Claim Durability Boundary

Three choreographer verbs share a **claim-then-assemble** shape: they write a claim (and optionally an evidence-inspected stamp), then build an advisory evidence payload before returning. The claim write must be **committed before evidence assembly begins**, so a cancelled or timed-out request leaves the claim standing and the retry resumes into an already-claimed task instead of re-racing for it.

## The problem this closes

Before the fix, the claim write and the evidence assembly ran in one request-scoped transaction. The claim methods (`qa_claim`, `pr_gate_claim`, `doc_claim`) call `session.flush()` but not `session.commit()` — the commit was deferred to the route layer's `get_db` auto-commit. The evidence legs can take the whole 120s verb budget. When the request was cancelled mid-assembly (the 120s `flow_verb_timeout_seconds` bound), `get_db` caught the `CancelledError` and invalidated the session, discarding the flushed-but-uncommitted claim. The retry then re-raced for the task, burning a review round plus a respawn (be-qa reported this on 2026-07-29: tasks `c2d8f8fa`, `ec7e3986`, `3b015e7a` rolled back to unclaimed on every attempt).

## The boundary

After the claim write (and `mark_evidence_inspected` where applicable), the verb calls `await self.task.session.commit()` **before** awaiting the evidence builder. The session factory uses `expire_on_commit=False`, so ORM objects stay valid after the explicit commit and can be passed straight to evidence assembly without re-fetching.

**Why `session.commit()` and not `begin_nested()`:** a savepoint release does not make the write durable — the outer transaction still needs to commit, which is exactly the step the timeout prevents. Only a full `session.commit()` lands the claim in its own transaction.

## The three verbs

### claim_review — `roboco/services/gateway/choreographer/qa.py`

The canonical pattern. After `qa_claim` + `mark_evidence_inspected`, commits, then builds evidence via `_build_qa_claim_evidence`. The `mark_evidence_inspected` stamp rides in the same commit as the claim so it also survives a cancellation.

### claim_gate_review — `roboco/services/gateway/choreographer/pr_gate.py`

Same boundary, no `mark_evidence_inspected` call (the gate path has no evidence-inspected stamp). After `pr_gate_claim`, commits, then builds evidence via `_build_gate_review_evidence`.

### claim_doc_task — `roboco/services/gateway/choreographer/doc.py`

Same boundary, but the workspace checkout leg runs between the claim commit and the evidence assembly. The claim commits before the (best-effort, `contextlib.suppress`) checkout and `_claim_doc_evidence`.

## Retry semantics

The same `active_claimant_id` check that guards the claim also drives retry behavior:

- **Same-agent retry returns evidence, not conflict.** If `to_python_uuid(t.active_claimant_id) == agent_id` (a prior attempt committed the claim but the evidence assembly timed out), the verb **skips the re-claim** and goes straight to evidence rebuild — returning success with the evidence payload. The underlying `_qa_or_doc_claim` idempotency guard already allows same-agent re-claims; the verb body's `active_claimant_id` check just short-circuits before calling it.

- **Different-agent retry is still refused.** A claim attempt by a different agent on an already-claimed task returns `None` from the claim method (the competing-claimant guard in `_qa_or_doc_claim` blocks it). The verb emits the existing rejection envelope: `not_authorized` for `claim_review` / `claim_doc_task`, `invalid_state` for `claim_gate_review` (the pre-existing shape). The fix does not widen claim stealing.

## The pattern in each verb body

All three follow the same shape:

```python
# Durability boundary: commit the claim BEFORE the advisory evidence
# assembly begins — see claim_review (qa.py) for the full rationale.
# Same-agent retry: if the task is already claimed by THIS agent,
# skip the re-claim and go straight to evidence rebuild.
if to_python_uuid(t.active_claimant_id) != agent_id:
    claimed = await self.task.<claim_method>(agent_id, task_id)
    if claimed is None:
        return await self._emit_rejection(...)   # different-agent refusal
    t = claimed
    await self.task.session.commit()              # the durability boundary

# ... evidence assembly (advisory, may take the whole 120s budget) ...
```

A separate `claimed` variable holds the claim result so the preflight task `t` is not clobbered to `None` before the rejection envelope's `with_introspection(task=t)` — `pr_gate.py` already used this shape; `qa.py` and `doc.py` were aligned to it in the round-2 QA fix.

## Advisory evidence legs are unchanged

The durability boundary does not change evidence-gap behavior. Advisory legs in `_build_qa_claim_evidence`, `_build_gate_review_evidence`, and `_claim_doc_evidence` still degrade into `evidence_gaps` entries via `run_bounded_leg` instead of failing the verb. The boundary only moves the commit earlier; the evidence assembly phase is still best-effort.

## Adding a fourth claim-then-assemble verb

If a new verb follows the claim-then-assemble shape:

1. After the claim write (and any evidence-inspected stamp), call `await self.task.session.commit()` **before** any evidence assembly or other long-running advisory work.
2. Guard the claim with `to_python_uuid(t.active_claimant_id) != agent_id` so a same-agent retry skips the re-claim and routes straight to evidence rebuild.
3. Use a separate `claimed` variable for the claim result so a `None` return (different-agent refusal) does not clobber the preflight task before the rejection envelope's introspection.
4. Keep the different-agent refusal as the existing `not_authorized` / `invalid_state` envelope — do not widen claim stealing.

## Tests

- **Integration regression** (`tests/integration/test_claim_durability_boundary.py`): each test seeds a task, commits the claim, simulates the cancellation by discarding the session, opens a fresh session, and asserts the claim (`active_claimant_id` + `qa_evidence_inspected` where applicable) is still in the DB. Three post-commit-cancellation tests (qa, gate, doc) plus same-agent idempotent re-claim and different-agent refused tests.
- **Unit tests** (`tests/unit/gateway/test_claim_durability_boundary.py`): verify the claim method is NOT called on same-agent retry, and IS refused on different-agent retry, for all three verbs.

## Related files

- **Implementation:** `roboco/services/gateway/choreographer/qa.py` (claim_review), `roboco/services/gateway/choreographer/pr_gate.py` (claim_gate_review), `roboco/services/gateway/choreographer/doc.py` (claim_doc_task)
- **Claim methods:** `roboco/services/task.py` — `_qa_or_doc_claim` (the shared idempotency guard), `pr_gate_claim`, `mark_evidence_inspected`
- **Session lifecycle:** `roboco/api/deps.py` (`get_db`, `expire_on_commit=False`)
- **Tests:** `tests/integration/test_claim_durability_boundary.py`, `tests/unit/gateway/test_claim_durability_boundary.py`