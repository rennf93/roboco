# Release proposal card: reject error toast shows the route's detail

**Date:** 2026-11-03 **Task:** d4763d1c **PR:** #1405 **Files:** `panel/src/components/dashboard/release-proposal-card.tsx`

## What changed

The reject mutation's `onError` in `ReleaseProposalCard` now builds its toast with `getErrorMessage(error)` from `@/lib/api/client` instead of `error.message`:

```tsx
onError: (error) => {
  toast.error(`Reject failed: ${getErrorMessage(error)}`);
},
```

## Why

The axios response interceptor in `panel/src/lib/api/client.ts` rejects with the raw `AxiosError`. Its `message` is the generic `Request failed with status code 409`, so a refused reject showed the CEO `Reject failed: Request failed with status code 409` and hid the reason. The release route puts the reason in `response.data.detail` (for example `Reject refused (already_in_progress): ...`, or the already-published message). `getErrorMessage` reads that field, so the toast now carries it.

This matches the Telegram approvals path (`panel/src/components/tg/approvals/release-detail.tsx`), which already used `getErrorMessage`. The backend route and the Telegram path were not touched.

## Behavior

| Reject fails with | Toast |
|---|---|
| 409 with `response.data.detail` | `Reject failed: <detail>` (string, validation array, or envelope object is formatted, never `[object Object]`) |
| Request timeout (`ECONNABORTED`) or network error (`ERR_NETWORK`) | `Reject failed: ` plus the friendly timeout / cannot-connect message |
| Other errors without a detail | `Reject failed: ` plus a fixed message for 401/403/404/422/5xx, otherwise the `Error.message` (or "An unexpected error occurred" for a non-Error) |

Approve and certificate-download error handling are unchanged.

## Test

`panel/src/components/dashboard/__tests__/release-proposal-card-status-feedback.test.tsx` gained "toasts the route's 409 detail when reject fails". It rejects the mocked reject call with an `AxiosError` carrying a 409 response whose `data.detail` is a recognizable string, walks the Reject-with-changes dialog, and asserts `toast.error` was called with `Reject failed: <detail>`.

## Related

- `docs/frontend/components/release-proposal-card-certificate.md` — the card's other mutations and toast conventions.
