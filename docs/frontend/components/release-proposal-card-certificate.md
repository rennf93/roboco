# Download Certificate Action (Release Proposal Card)

**Date:** 2026-09-05 **Task:** bfb48210 **PR:** #1015 **Files:** `panel/src/lib/api/release.ts`, `panel/src/components/dashboard/release-proposal-card.tsx`

## What

The CEO's release-proposal card (`release-proposal-card.tsx`) gains a "Download certificate" button in its Approve/Reject actions row. It is the frontend half of a two-cell feature; the backend half (`GET /api/releases/{version}/certificate`) shipped earlier in task `63375b3c` (PR #969) — see `docs/backend/api/release-certificate-endpoint.md` for the endpoint's own doctrine (version-lookup semantics, CEO gate, response-field provenance). This doc covers only the panel side: the typed client method and the button/download/toast wiring.

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
- `panel/src/components/dashboard/__tests__/release-proposal-card-certificate.test.tsx` — a new test file (sibling to the pre-existing `release-proposal-card.test.tsx`) that mocks only `releaseApi` and `sonner`, deliberately leaving `@tanstack/react-query` UN-mocked so the real `useMutation` `onSuccess`/`onError` callbacks run: the Blob-download-trigger path (asserts `URL.createObjectURL`/`revokeObjectURL` and the anchor's `click()`), the unpublished-toast path, and the genuine-error-toast path.

## Related

- `docs/backend/api/release-certificate-endpoint.md` — the backend half: response-schema field provenance, version-lookup/CEO-gate semantics, release task-set derivation heuristic.
- `docs/map/panel.md` / `docs/map/_complete_map.md` — the `releaseApi` module and `ReleaseProposalCard` component rows now name `getCertificate`/"Download certificate" alongside the pre-existing proposal approve/reject entries.
