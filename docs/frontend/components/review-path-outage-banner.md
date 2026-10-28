# Review-path outage banner

A read-only banner mounted in the dashboard layout. It shows while the backend reports an active review-path tool outage (evidence and git-readonly reads failing across tasks) and disappears when the backend reports recovery. It only surfaces state. Remediation stays with the PM and CEO, so the banner has no buttons, links, or handlers.

## API contract

This is the frontend half of a cross-cell contract. The backend cell owns detection and the exact schema, and any deviation is re-synced through main-pm and lands in `panel/src/lib/api/system-health.ts` first.

```
GET /api/system-health/review-path -> ReviewPathOutageStatus
```

```ts
type OutageType = "review_path_tools";

interface ReviewPathOutageStatus {
  active: boolean; // true while the outage is ongoing
  outage_type: OutageType; // currently always "review_path_tools"
  window_start: string | null; // ISO 8601 start of the outage window; null when no outage
  recovered: boolean; // true once reads recovered and the backend cleared the event
}
```

`systemHealthApi.getReviewPathOutage()` returns `null` on a 404. This means the backend surface has not been merged yet, or a proxy strips the route. Callers then degrade to "no banner" instead of erroring, so the banner never becomes its own outage. Any other error (for example a 500) is rethrown to react-query.

## Files

| File                                                              | Role                                                                                                                  |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `panel/src/lib/api/system-health.ts`                              | The contract: `ReviewPathOutageStatus`, `OutageType`, and the `systemHealthApi` client with its 404-to-`null` degradation. |
| `panel/src/hooks/use-review-path-outage.ts`                       | `useReviewPathOutage` (react-query poll every 30s) and the `systemHealthKeys` query-key factory.                      |
| `panel/src/components/health/review-path-outage-banner.tsx`       | `ReviewPathOutageBanner`, the presentational red alert row.                                                           |
| `panel/src/app/(dashboard)/layout.tsx`                            | Mounts the banner below `RateLimitBanner`, above `<main>`.                                                            |

## Behavior

`useReviewPathOutage` returns one of three shapes:

| Hook value                 | Meaning                                         | Banner               |
| -------------------------- | ----------------------------------------------- | -------------------- |
| `undefined`                | First poll still in flight                      | Nothing rendered     |
| `null`                     | Backend surface not merged yet (404)            | Nothing rendered     |
| `{ active: false, ... }`   | No outage, or recovered                         | Nothing rendered     |
| `{ active: true, ... }`    | Outage ongoing                                  | Red alert row        |

While active, the banner renders a `role="alert"` row (`aria-live="polite"`, `aria-label="Review-path tool outage"`) with a warning icon, the title "Review-path tools outage", the window start ("since" plus `formatAbsoluteTimestamp`, omitted if `window_start` is null), and a right-aligned note that evidence and git reads may fail and reviews resume automatically on recovery.

The banner clears without a page reload. Once the poll returns `active: false`, the component renders `null`.

## Delivery choice: polling

The hook polls every 30 seconds, the same cadence as the maintenance-pause and rate-limit banners. The alternatives were considered and rejected:

- **Bootstrap payload**: stale until a reload, so the banner would not clear on recovery.
- **WebSocket push on `/ws/system`**: lowest latency, but it needs a backend event the panel cannot assume has landed.
- **Polling**: self-clears, matches the existing banner idiom, stays confined to `panel/`, and degrades quietly through the 404 path. The cost is up to 30s of detection lag.

## Design bar

The banner reuses the existing red alert idiom (`border-red-300 bg-red-50`, dark-mode `red-800`/`red-950`) used for hard-stop banners, with no new tokens or motion.

## Testing

```bash
cd panel
pnpm test review-path-outage
pnpm test system-health
```

Covered behaviors, all against a mocked contract because the backend slice can land after the panel:

- **Banner** (`review-path-outage-banner.test.tsx`):
  - Renders while active, with the title and "since".
  - Renders nothing when inactive and recovered.
  - Renders nothing for `null` (surface not merged) and for `undefined` (first poll in flight).
  - Disappears on rerender when active flips to recovered.
  - Renders no `button` role, which is the read-only guarantee.
- **API client** (`system-health.test.ts`):
  - Returns the status payload from `GET /system-health/review-path`.
  - Degrades to `null` on a 404.
  - Rethrows non-404 errors.
