# API rate limiting and retry behavior

The API client (`panel/src/lib/api/client.ts`) gates retries on HTTP 429 (rate limit) responses by HTTP method to prevent accidental duplicate side effects from replayed requests.

## Automatic retries (safe methods)

**GET** and **PUT** requests automatically retry up to 3 times on a 429 response:

- **GET** has no side effect — retrying is always safe.
- **PUT** is a full-resource replace — replaying it is a no-op past the first apply (idempotent).

When a retry succeeds, the user sees a toast: `"Rate limited by <provider>. The system has paused operations and will resume automatically in ~Ns."`

When retries are exhausted, the same toast appears, but the operation completes (after backoff delay).

## Manual retries (stateful methods)

**POST**, **PATCH**, and **DELETE** requests do NOT auto-retry on a 429, because:

- **POST** can create a duplicate resource if replayed.
- **PATCH** can double-apply a partial update.
- **DELETE** can be replayed, causing confusion about the resource's state.

A stateful request that hits a 429 fails immediately with a toast: `"Rate limited by <provider>. This action was not automatically retried to avoid duplicating it — please try again in ~Ns."`

## Idempotency-key override (client-side gate only, not a working feature yet)

`isRetrySafe` also accepts an `X-Idempotency-Key` header as an override: a POST/PATCH/DELETE request carrying that header is treated as retry-safe and auto-retries on a 429 the same as GET/PUT.

This is client-side scaffolding, not a live contract today:

- No call site in the panel sets `X-Idempotency-Key` — every POST/PATCH/DELETE in the app currently takes the no-retry path above.
- There is no backend support for the header at all. The API does not store or check idempotency keys, so nothing would deduplicate a replayed request even if one were retried this way.

Idempotency-key retry — the client attaching a key and the backend storing it to return a cached result on a repeat — is a **possible future enhancement**, not something implemented server-side. Don't set this header expecting deduplication; until the backend catches up, it would only unlock a client-side retry with no safety net behind it.

## Implementation notes

The retry decision is made by the exported `isRetrySafe(config)` function, which checks:

1. The HTTP method (case-insensitive).
2. For POST/PATCH/DELETE, the presence of the `X-Idempotency-Key` header.

This pattern is tested in `panel/src/lib/__tests__/client.test.ts` and enforced at the request-interceptor level in `client.ts`.
