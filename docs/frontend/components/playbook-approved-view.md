# Playbook review: Approved view and archive

The dashboard's Playbook Review card now has a Drafts / Approved toggle. Drafts keep the existing approve/reject flow. Approved lists indexed playbooks and lets the CEO archive a stale or wrong one. Archiving also removes the playbook from the knowledge base, so agents stop being offered it.

## Files

| File | Role |
|------|------|
| `panel/src/lib/api/playbooks.ts` | Typed client. Adds `listApproved()` and `archive(id)` next to `listDrafts`, `approve`, `reject`. |
| `panel/src/components/dashboard/playbook-review-queue.tsx` | The card. Hosts the `SegmentedControl` toggle (`Drafts` / `Approved`, default `Drafts`) and the drafts view. |
| `panel/src/components/dashboard/playbook-approved-list.tsx` | `PlaybookApprovedList`: the Approved view, confirm dialog and archive mutation. |
| `panel/src/lib/api/__tests__/playbooks.test.ts` | Client tests. |
| `panel/src/components/dashboard/__tests__/playbook-approved-list.test.tsx` | Approved view tests. |
| `panel/src/components/dashboard/__tests__/playbook-review-queue.test.tsx` | Card and toggle tests. |

## Client API

| Method | Request | Returns |
|--------|---------|---------|
| `playbooksApi.listApproved()` | `GET /playbooks` with `status=approved` | `Playbook[]` |
| `playbooksApi.archive(id)` | `POST /playbooks/{id}/archive` | the archived `Playbook` |

These call the existing backend routes (`roboco/api/routes/playbooks.py`); no backend change shipped. The archive route returns `409` if the playbook is already archived.

## Approved view behavior

- **List:** each row shows title, team badge (when set), tag badges and the `problem` text (labelled "When:"). The procedure body is not shown here.
- **States:** two skeleton rows while loading (`data-testid="approved-loading"`); "No approved playbooks yet." when empty; a `role="alert"` message "Could not load approved playbooks." on error.
- **Refresh:** the query key is `["playbooks", "approved"]` and refetches every 30 seconds.
- **Archive:** the row's Archive button opens a confirmation dialog naming the playbook. Cancel closes it. "Confirm archive" runs the mutation and is disabled while pending.
- **On success:** toasts "Playbook archived and removed from the index", invalidates both `["playbooks", "drafts"]` and `["playbooks", "approved"]`, closes the dialog, and the row disappears on refetch.
- **On 409:** treated as non-fatal. Both keys are still invalidated (so the stale row drops), a `toast.info` says "That playbook was already archived", and the view keeps rendering.
- **Other errors:** `toast.error("Archive failed: ...")`; the row stays.

## Behavior change to know about

The review card used to return nothing when there were no drafts. It now always renders, because otherwise the Approved view would be unreachable when no drafts are pending. With zero drafts the Drafts tab shows "No drafted playbooks awaiting review." The pending-count badge still reflects drafts only.

## Test coverage

Client tests cover `listApproved` (params) and `archive` (URL, method). Approved view tests cover list rendering, loading, empty and error states, confirm-then-archive success (toast and both invalidations), and the 409 path. Queue tests cover the Drafts/Approved toggle (default Drafts, switch to Approved and back) alongside the per-row approve lock.

## Not documented / out of scope

No backend or API-contract docs: the routes are unchanged. There is no unarchive action in the panel.
