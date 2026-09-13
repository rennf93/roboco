# Download Certificate Action (Release Proposal Card)

**Date:** 2026-09-06 **Task:** bfb48210, 13af9490, e18d8f49, 25fa7f8a **PR:** #1015; post-publish reachability fix in PR #1028 (task `13af9490`); task-status-polling rewrite in task `e18d8f49`; round-2 pr_gate pointer-mirroring fix + mid-mount transition test in PR #1031/#1063 (task `25fa7f8a`) **Files:** `panel/src/lib/api/release.ts`, `panel/src/components/dashboard/release-proposal-card.tsx`

## What

The CEO's release-proposal card (`release-proposal-card.tsx`) gains a "Download certificate" action. It is the frontend half of a two-cell feature; the backend half (`GET /api/releases/{version}/certificate`) shipped earlier in task `63375b3c` (PR #969) — see `docs/backend/api/release-certificate-endpoint.md` for the endpoint's own doctrine (version-lookup semantics, CEO gate, response-field provenance). This doc covers the panel side: the typed client method, the button/download/toast wiring, and the task-status-polling mechanism (task `e18d8f49`, superseding the `13af9490` approve-response-keyed attempt below) that keeps the control reachable once the proposal it was drawn from disappears.

## The task-status-polling mechanism (task e18d8f49)

The certificate endpoint only ever serves a COMPLETED/published version, and `GET /release/proposal` — the query backing the open-proposal card — explicitly excludes COMPLETED/CANCELLED proposals. So there is never a moment where an open-proposal control's target version (`report.proposed_version`) and the endpoint's servable version coincide: the instant a publish succeeds, `getProposal()` 404s to `null` and the open-proposal card unmounts. There is exactly ONE "Download certificate" control in the whole component, gated on a server-confirmed published state — never the old always-visible open-proposal button.

An earlier attempt (task `13af9490`, described below for history) tried to bridge this by stashing `{version, release_url}` from the `approveMutation`'s success response the moment `result.status === "published"`. That branch was dead code: `POST /release/proposal/approve` (`roboco/api/routes/release.py:44-92`) is a 202-dispatch route that ALWAYS returns `{status: "accepted", version: "", release_url: null}` synchronously — the real publish runs ~40 minutes later in a background task (`dispatch_approve`). `result.status` is never `"published"` in production, so the confirmation card it would render never appeared, and the component-local state it depended on had no persistence anyway — a reload or navigation during the ~40min background publish lost the pointer.

The fix persists a `{taskId, version}` pointer instead of trusting the approve response:

- The moment a proposal with a `report` loads (well before the CEO ever clicks Approve — the panel already knows the target version and the proposal's own `task_id` from `ReleaseProposalResponse`), an effect writes `{taskId: proposal.task_id, version: report.proposed_version}` to `localStorage` (key `roboco.release-certificate-pointer`).
- The same derivation (`derivedPointer`) is ALSO mirrored into the `storedPointer` React state — a render-time, ref-guarded sync (`lastDerivedKeyRef`, compared each render, `setStoredPointer` called only when the derived key actually changes), not a `setState` inside the write-through effect above, because the React Compiler ESLint rule (`react-hooks/set-state-in-effect`) blocks a synchronous `setState` call inside an effect body. Without this mirror, `storedPointer` was seeded only once, by the mount-time lazy `useState` initializer below — a session that mounted with empty `localStorage` kept it `null` for the session's whole life, and a session that mounted with a previous cycle's pointer already in storage kept THAT stale pair for the whole session. Mirroring the derivation on every render closes both: a fresh session picks up the just-derived pointer instead of staying `null`, and a real publish can never fall back to a stale prior-cycle pointer, because `storedPointer` is kept current with `derivedPointer` for as long as a proposal is open.
- The card's `pointer` is derived each render: the open proposal's own `{task_id, report.proposed_version}` when a proposal is present, else the pointer last read from `localStorage` (read once via a lazy `useState` initializer, so a fresh mount — a reload or navigation — re-confirms it against the server rather than trusting it as-is) and kept in sync thereafter by the mirror above.
- A `useQuery` polls `GET /tasks/{taskId}` (`tasksApi.get`, the existing generic task-detail route — no new backend endpoint) whenever a pointer is set, immediately on mount and then every 30s (mirroring the open-proposal query's own `refetchInterval`), until the task reaches a terminal status (`refetchInterval` returns `false` once `completed` or `cancelled`).
- When the polled task's `status === "completed"`, the release-manager's own task — the SAME task the whole release cycle rides end to end — has genuinely finished, and the "Published vX.Y.Z" confirmation `Card` renders with its "Download certificate" button wired to `certificateMutation.mutate(pointer.version)`.
- If the polled task instead reaches a terminal non-completed status (`cancelled`), the pointer will never resolve to a certificate, so it's cleared from `localStorage` and local state — this is done as the same kind of render-time state adjustment (comparing against a `useRef`-tracked previous status), not inside a `useEffect`, for the same ESLint-rule reason as the mirror above.

**Round-2 pr_gate fix (F-85c72990 / F-d3d15afd).** The two bugs above shipped because every test in the suite pre-seeded `localStorage` and mocked `getProposal` to `null` BEFORE the first render — the component always mounted with the pointer already in state, so no test ever exercised losing a live proposal mid-mount. The added mid-mount transition test below (F-96e5436e) closes that gap.

The card's empty-state guard is `if (!proposal && !showCertificateCard) return null;`, where `showCertificateCard` is `!!pointer && pointerTaskStatus === "completed"` — the normal empty state (no open proposal, nothing confirmed published) still hides the card exactly as before, while a confirmed-published pointer keeps the confirmation card mounted independently of whatever the open-proposal query returns next.

## The post-publish reachability fix (task 13af9490, superseded)

The button described in the section below lived inside the open-proposal card and targeted `report.proposed_version` — a version the certificate endpoint, by construction, never serves while that proposal is still open. `ReleaseProposalCard` held a second, independent piece of `useState`:

```ts
const [publishedRelease, setPublishedRelease] = useState<{
  version: string;
  releaseUrl: string | null;
} | null>(null);
```

`approveMutation`'s `onSuccess` set it from the `ReleaseExecuteResult` the moment `result.status === "published"` — a status the real approve route can never return (see above), so this mechanism was unreachable in production and was replaced entirely by the task-status-polling mechanism above.

## The API client method

`panel/src/lib/api/release.ts` adds `releaseApi.getCertificate(version: string): Promise<ReleaseCertificate | null>`, mirroring the file's existing `getProposal` 404-as-null convention byte for byte: a plain `try { return (await api.get(...)).data } catch`, where a caught `AxiosError` with `response?.status === 404` returns `null` and any other error rethrows. The four interfaces (`ReleaseCertificate`, `ReleaseCertificateTaskState`, `ReleaseCertificateFindingsSummary`, `ReleaseCertificateSeverityCounts`) are typed field-for-field against the backend's `ReleaseCertificateResponse` Pydantic schema (`roboco/api/schemas/release.py`) — this is a cross-cell contract, so a future backend-side additive field needs a matching additive field here, never a rename/reshape on either side without touching both.

## The button + download flow

`ReleaseProposalCard` wires a TanStack Query `useMutation` (`certificateMutation`) whose `mutationFn` calls `releaseApi.getCertificate(report.proposed_version)`:

- **Success, certificate present:** `downloadCertificate()` (a local helper, not exported) builds a `Blob` from `JSON.stringify(certificate, null, 2)`, creates an object URL via `URL.createObjectURL`, clicks a temporary `<a download="release-certificate-{version}.json">` anchor, then revokes the URL. No server-side file is ever written — the download is entirely client-side.
- **Success, `null` (unpublished version, 404):** a `sonner` info toast ("This version hasn't published yet — no certificate available.") — the CEO never sees a thrown error for a routine not-ready state.
- **A genuine fetch error** (network drop, 500, etc.): a `sonner` error toast naming the underlying `Error.message`, again never an unhandled throw.

The button itself sits alongside the existing Approve/Reject actions (`flex-col-reverse ... sm:flex-row` row), reuses the card's existing `HelpTip` + shadcn/ui `Button` + `lucide-react` `Download` icon conventions (no new UI library), and disables itself while `certificateMutation.isPending`. There is no separate frontend auth check — the card is already CEO-only surface area, and the backend's `_require_ceo` + 404-for-unpublished combination is what the null/toast branch is handling.

## Tests

- `panel/src/lib/api/__tests__/release.test.ts` — `getCertificate`'s happy path (verbatim pass-through of the mocked axios response), 404-to-null, and non-404 rethrow.
- `panel/src/components/dashboard/__tests__/release-proposal-card-certificate.test.tsx` — a test file (sibling to the pre-existing `release-proposal-card.test.tsx`) that mocks `releaseApi`, `tasksApi`, and `sonner`, deliberately leaving `@tanstack/react-query` UN-mocked so the real `useMutation`/`useQuery` callbacks run. One describe block asserts the real `POST /release/proposal/approve` contract (`{status: "accepted", version: ""}` and its "dispatched — running in the background" toast — never a "published" assertion off the approve response). A second describe block covers the task-status-polling mechanism: no "Download certificate" control renders while the proposal's task is still open/non-terminal (proving the old always-visible button is gone); seeding `localStorage` with a stored `{taskId, version}` pointer and mocking `tasksApi.get` to resolve `status: "completed"` renders exactly one "Download certificate" control that calls `getCertificate` with the stored version (covers the reload/navigation-survives case directly, since the pointer is read from `localStorage` rather than derived from an in-memory approve response); the 404-to-null and genuine-fetch-error toast paths reuse this same completed-task setup; a `status: "cancelled"` task confirms the stored pointer is cleared from `localStorage` instead of polling forever; and a final test (added for F-96e5436e) mounts with an OPEN, non-terminal proposal and NO pre-seeded `localStorage`, then transitions `getProposal` to `null` and `tasksApi.get` to `completed` mid-session and asserts the confirmation card still resolves to exactly one "Download certificate" control targeting the just-published version — the one test in the suite that actually loses a live proposal mid-mount, proving the render-time pointer mirror above rather than re-testing the pre-seeded paths the earlier tests already covered.
- `panel/src/components/dashboard/__tests__/release-proposal-card-status-feedback.test.tsx` dropped its "published" per-status toast case (that status can never actually reach the approve response — see above); its other per-status cases (`already_in_progress`, `redis_unavailable`, `lock_lost`, `gate_failed`, `accepted`) are unchanged.

## Related

- `docs/backend/api/release-certificate-endpoint.md` — the backend half: response-schema field provenance, version-lookup/CEO-gate semantics, release task-set derivation heuristic.
- `docs/map/panel.md` / `docs/map/_complete_map.md` — the `releaseApi` module and `ReleaseProposalCard` component rows now name `getCertificate`/"Download certificate" alongside the pre-existing proposal approve/reject entries.
