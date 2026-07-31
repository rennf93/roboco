# Evidence-Assembly Timeout Fix (claim_review / evidence() / roboco_git_diff)

`claim_review` (QA's task claim) and `evidence()` (the read-only inspection verb) share an "evidence-assembly" segment — git diff/fetch, the conventions validator, and a handful of DB reads — that used to duplicate work badly enough to blow the outer server-side verb timeout (KB err-4b56d227a778). This fix dedupes the git work, parallelizes the independent pieces, bounds the conventions-validator subprocess, and wraps the whole segment in its own timeout that returns a structured, named error instead of a bare rollback.

## What was duplicated

Before this fix, `content_actions.evidence()` and `qa._build_qa_claim_evidence` (claim_review's evidence builder) each called `GitService.diff()` and then `GitService.list_changed_files()` independently. Both methods separately re-resolved the workspace, auth token, head ref, and diff base, then each ran its own full `git diff` subprocess — the same workspace fetch and diff computation done twice per request. `_build_qa_claim_evidence` additionally fed `list_changed_files` into `conventions_check_for_task`, which called `list_changed_files` a THIRD time, then ran the conventions-validator subprocess with its own independent 120s timeout nested inside the outer verb's own 120s budget — on its own enough to exhaust the whole request.

## The fix

**`GitService.diff_and_files(branch_name, base=None, actor_agent_id=None, preferred_parent=None)`** (`roboco/services/git.py`) is the new combined accessor: it resolves the workspace/token/head-ref/diff-base exactly once, then runs `git diff` and `git diff --name-only` concurrently via `asyncio.gather`, returning `(diff_text, files_changed)`. The existing `diff()` and `list_changed_files()` keep their original signatures and behavior unchanged for callers that only need one of the two (`roboco_git_diff`, `doc.py`'s evidence path) — `diff_and_files` is additive, not a replacement.

`conventions_check_for_task` (same file) now accepts an optional `changed_files` parameter; when the caller (`_build_qa_claim_evidence`) already has the file list from `diff_and_files`, it's passed straight through instead of triggering a third `list_changed_files` call.

The conventions-validator subprocess timeout is now `settings.conventions_validator_timeout_seconds` (default 45s) instead of the old hardcoded 120s, and in claim_review the git+conventions segment runs concurrently with the DB-reads segment (see below) rather than serially after it.

Both `evidence()` and `_build_qa_claim_evidence` batch their independent DB reads (journal highlights, ancestor context, open findings, and — claim_review only — the full findings ledger) into one coroutine each (`_evidence_db_reads` / `_qa_db_reads`), and that whole DB-reads batch runs concurrently with the git+conventions work via `asyncio.gather`. The DB reads stay sequential *within* their own coroutine on purpose: they all read through the same request-scoped `AsyncSession`, and asyncpg cannot serve two in-flight queries on one connection at once (`IllegalStateChangeError`) — the win is git-work-vs-DB-work overlap, not fanning out the DB reads themselves.

## The bounded-timeout guard

A new `settings.evidence_assembly_timeout_seconds` (default 90s, well under the outer `flow_verb_timeout_seconds` of 120s) wraps the remaining evidence-assembly work in three places:

- `ContentActions.evidence()` — wraps the `asyncio.gather` of the git-diff coroutine and `_evidence_db_reads`.
- `QAMixin.claim_review()` — wraps the call to `_build_qa_claim_evidence`; the claim itself (`qa_claim` + `mark_evidence_inspected`) has already committed by this point, so a trip here means only evidence assembly is slow, not that the claim failed.
- `GET /api/git/diff` (`roboco/api/routes/git.py`, `roboco_git_diff`) — wraps the diff + `--stat` subprocess pair.

On a trip, each site returns a structured `Envelope.gateway_timeout(component, timeout_seconds, remediate)` (new classmethod on `roboco/services/gateway/envelope.py`'s `Envelope`) naming which segment stalled (e.g. `"git diff/fetch or a journal/ancestor/findings DB read"`) and a remediation hint, instead of the outer 120s rollback with no indication of which piece stuck. The route's HTTP path raises a `504 Gateway Timeout` with the equivalent detail message.

## New settings (`roboco/config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `evidence_assembly_timeout_seconds` | `90.0` | Bounded inner-work timeout for the evidence-assembly segment of `claim_review` / `evidence()` / `roboco_git_diff`. |
| `conventions_validator_timeout_seconds` | `45` | Timeout for the conventions-validator subprocess (`python -m roboco.conventions check`), kept comfortably below `evidence_assembly_timeout_seconds` so a hung validator can't by itself exhaust the outer verb's budget. |

## Timing instrumentation

Each segment logs its own duration (structlog `.info()` calls) so a slow request's dominant segment is identifiable from logs alone:

- `GitService.diff_and_files` logs `resolve_ms` (workspace/token/head/base resolution) and `diff_ms` (the concurrent diff subprocesses) as "evidence diff_and_files timing".
- `conventions_check_for_task` logs `total_ms` plus whether it reused the passed-in file list, as "conventions_check_for_task timing".
- `evidence()` logs `git_diff_and_fetch_ms` ("evidence git diff/fetch timing") and the DB-reads batch logs `db_reads_ms` ("evidence db reads timing").
- `claim_review`'s two segment helpers (`_qa_git_and_conventions`, `_qa_db_reads`) log `git_diff_and_fetch_ms`/`conventions_ms` ("claim_review git+conventions timing") and `db_reads_ms` ("claim_review db reads timing") respectively.

## Related files

- **Implementation:** `roboco/services/git.py` (`diff_and_files`, `conventions_check_for_task`, `_run_conventions_validator`), `roboco/services/gateway/content_actions.py` (`evidence`, `_evidence_db_reads`), `roboco/services/gateway/choreographer/qa.py` (`claim_review`, `_build_qa_claim_evidence`, `_qa_git_and_conventions`, `_qa_db_reads`, `_qa_convention_findings`), `roboco/services/gateway/envelope.py` (`Envelope.gateway_timeout`), `roboco/api/routes/git.py` (`get_git_diff`), `roboco/config.py` (the two new `Settings` fields).
- **Unaffected on purpose:** `roboco/services/gateway/evidence_builder.py` stays pure (no DB/git calls) per its module docstring — `build_evidence_for_task` still just assembles the already-fetched pieces into the evidence payload.
- **Tests:** `tests/unit/services/test_git_diff_and_files.py` (proves the shared workspace/token/head/base state resolves exactly once), `tests/unit/gateway/test_choreographer_qa.py` and `tests/unit/gateway/test_content_actions.py` (bounded-timeout trips return `gateway_timeout`), `tests/integration/test_git_routes.py` (the `/api/git/diff` route's bounded-timeout guard), `tests/unit/services/test_git_conventions_check_fail_closed.py`.
