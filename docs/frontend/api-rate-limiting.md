# API rate limiting and retry behavior

The API client (`panel/src/lib/api/client.ts`) gates retries on HTTP 429 (rate limit) responses by HTTP method to prevent accidental duplicate side effects from replayed requests.

## Automatic retries (safe methods)

**GET** and **PUT** requests automatically retry up to 3 times on a 429 response:

- **GET** has no side effect — retrying is always safe.
- **PUT** is a full-resource replace — replaying it is a no-op past the first apply (idempotent).

When a retry succeeds, the user sees a toast: `"Rate limited by <provider>. The system has paused operations and will resume automatically in ~Ns."`

When retries are exhausted, the same toast appears, but the operation completes (after backoff delay).

## Manual retries (stateful methods)

**POST**, **PATCH**, and **DELETE** requests do NOT auto-retry on a 429 without an idempotency key header, because:

- **POST** can create a duplicate resource if replayed.
- **PATCH** can double-apply a partial update.
- **DELETE** can be replayed, causing confusion about the resource's state.

To safely retry these methods, the caller must provide an **idempotency key** that the backend can use to deduplicate:

```typescript
// Example: safe POST with idempotency key
const response = await api.post("/api/tasks", taskData, {
  headers: {
    "X-Idempotency-Key": "unique-idempotency-key-value",
  },
});
```

When an idempotency key is present, the request will auto-retry on a 429 (up to 3 times).

When a stateful request hits a 429 WITHOUT an idempotency key, it fails immediately with a toast: `"Rate limited by <provider>. This action was not automatically retried to avoid duplicating it — please try again in ~Ns."`

## Idempotency key format

The idempotency key should be a **unique, deterministic identifier** for the operation:

- A UUID (`crypto.randomUUID()`)
- A hash of the operation's intent (e.g., task ID + action name)
- A timestamp + action combination

The backend is responsible for storing and checking idempotency keys; if a request arrives twice with the same key, it should return the cached result instead of re-executing.

## Implementation notes

The retry decision is made by the exported `isRetrySafe(config)` function, which checks:

1. The HTTP method (case-insensitive).
2. For POST/PATCH/DELETE, the presence of the `X-Idempotency-Key` header.

This pattern is tested in `panel/src/lib/__tests__/client.test.ts` and enforced at the request-interceptor level in `client.ts`.
