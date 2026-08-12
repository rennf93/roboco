# Operator maintenance pause (navbar)

A navbar control plus a persistent banner that let the CEO halt new agent spawns for online maintenance, then resume, without killing in-flight work.

## API contract

CEO-only server-side; the panel's default identity headers (`lib/api/client.ts`, `X-Agent-ID`/`X-Agent-Role` defaulting to the CEO) already carry that, so no extra auth wiring lives in this module. Mirrors `roboco/api/routes/maintenance_pause.py` exactly.

```
GET    /api/maintenance-pause              -> MaintenancePauseStatus[]        (always all three scopes, one row each)
POST   /api/maintenance-pause/{scope}      body: PauseScopeRequest
                                            -> MaintenancePauseStatus          (the one scope just paused)
DELETE /api/maintenance-pause/{scope}      -> MaintenancePauseStatus          (the one scope just resumed; idempotent)
```

There is no batch endpoint: pausing several scopes at once from the dialog means one POST per scope. `scope` in the path is one of `dispatch` | `board_programs` | `engines`; an unknown scope 422s.

```ts
interface MaintenancePauseStatus {
  scope: string;
  paused: boolean;
  paused_by: string | null; // agent slug / CEO identity that issued the pause
  paused_at: string | null; // ISO 8601
  reason: string | null; // free text the operator gave, if any
  expires_at: string | null; // ISO 8601; always set on a paused row, the
  // backend has no indefinite pause
}

interface PauseScopeRequest {
  reason?: string;
  hours?: number; // > 0, <= 336 (14 days); server default 4.0
}
```

`paused` already resolves expiry server-side: a pause read back past its own `expires_at` reads as `paused: false`, no sweep required to lift it. Invalid `hours` or an unknown scope returns 422; a non-CEO caller gets 403.

The three scopes are `dispatch` (the normal dev/QA/PM/PR-reviewer claim-and-spawn loop), `board_programs` (the Board Program registry's cron/metric-triggered cycles), and `engines` (self-heal, CI-watch, dependency updates, release manager, docs sync, and similar background originators). `scope` stays a bare `string` on the wire, not a closed TS union, so a scope the backend adds later still round-trips through the list response and renders (falling back to its raw key as a label) instead of being dropped.

## Files

| File                                                       | Role                                                                                                                                                                                                                                   |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `panel/src/lib/api/maintenance.ts`                         | The contract: types, route paths, the `maintenanceApi` client, scope label/description constants, `maintenanceScopeStatus` lookup helper, `DEFAULT_PAUSE_HOURS`/`MAX_PAUSE_HOURS`.                                                     |
| `panel/src/hooks/use-maintenance.ts`                       | `useMaintenanceStatus` (30s poll, shared by the control and the banner), `usePauseMaintenanceScopes` (fires one POST per selected scope in parallel, reports per-scope success/failure), `useResumeMaintenance` (single-scope DELETE). |
| `panel/src/components/maintenance/maintenance-control.tsx` | Navbar icon button + pause dialog (scope checkboxes, duration select, optional reason).                                                                                                                                                |
| `panel/src/components/maintenance/maintenance-banner.tsx`  | Persistent high-contrast bar mounted below the header when any scope is paused; per-scope one-click Resume.                                                                                                                            |
| `panel/src/components/layout/header.tsx`                   | Mounts `MaintenanceControl` immediately after `ConnectionStatus`.                                                                                                                                                                      |
| `panel/src/app/(dashboard)/layout.tsx`                     | Mounts `MaintenanceBanner` immediately below `Header`, above `RateLimitBanner`.                                                                                                                                                        |

## UI behavior

- **Navbar control**: icon-only button next to the connection indicator. The icon and tooltip reflect four distinct states: loading (spinner, "Checking maintenance-pause status..."), error (amber triangle, names the fetch error, the dialog still opens so the operator is never blocked from acting), idle (muted wrench, "No maintenance pause active"), and active (red octagon, names the paused count). The accessible name (`aria-label`) folds in the paused count too, not just the tooltip, so a screen reader user gets the same signal.
- **Pause dialog**: lists the three scopes as checkboxes (an already-paused scope is disabled with an inline note pointing at the banner's Resume button instead), a duration select (30m / 1h / 2h / 4h / 14 days (maximum)), and an optional reason textarea. The default duration is 4 hours, matching the backend default. There is no "Indefinite" option: the backend caps every pause at 336 hours (14 days), so the longest choice is labeled honestly as the documented maximum rather than implying an unbounded pause that doesn't exist server-side. The primary button IS the confirmation step, no separate confirm-on-confirm; it stays disabled until at least one scope is checked. Cancel discards the form without calling the API.
- **Multi-scope pause is per-scope, not all-or-nothing**: picking several scopes fires one POST per scope in parallel and never aborts early, a 422 on one selection doesn't stop the others from being attempted, since the scopes are independent toggles. Every outcome is reported: an all-success submit closes the dialog with a success toast; a partial or total failure keeps the dialog open, shows an inline error under each failed scope's checkbox, leaves only the failed scopes checked (ready for a one-click retry), and surfaces a summary toast (`warning` for a partial success, `error` for a total failure). The status query is always invalidated after the attempt, never locally patched, so the checkboxes reflect exactly what the server has and the UI can never claim a scope is paused when its call actually failed.
- **Banner**: renders nothing when nothing is paused (the correct empty state, mirroring the existing `RateLimitBanner`). Renders a slim amber strip, not the full red banner, when the status fetch itself fails, since silently agreeing with "not paused" on an error would be worse than admitting the state is unknown. When paused, renders one row per scope with who paused it, when, the reason (if any), a live one-second countdown to auto-expiry, and a one-click Resume button for that scope alone. Because `paused` self-resolves server-side, the row's local countdown hitting zero never flips the UI to "resumed" on its own, it triggers exactly one refetch of the status query instead, so the banner's read of "is this still paused" always comes from the server, never from the client's own clock potentially disagreeing with it (clock drift, a tab backgrounded past its timer resolution).

## Design bar

Matches the existing navbar idiom rather than inventing a new visual language: same `Button variant="ghost" size="icon"` + `Tooltip` composition as the refresh button and theme toggle right next to it, same `Dialog`/`Checkbox`/`Select`/`Textarea` primitives used elsewhere (`feature-flags-card.tsx`, `board-programs-card.tsx`). The banner reuses `RateLimitBanner`'s per-row idiom (icon, `HelpTip`-wrapped fields, a right-aligned status readout, a live countdown ticking every second) but at a higher-contrast red instead of amber, since a maintenance pause is a harder stop than an auto-recovering rate limit. Motion stays static (hover/active only); no decorative pulsing.

## Testing

```bash
cd panel
pnpm test maintenance
pnpm test connection-status
```

Covered behaviors: the four distinct control states (loading/error/idle/active) via hover-driven tooltip assertions; the confirm-button disabled-until-a-scope-is-checked gate; duration mapping (the default 4-hour submit, and the maximum-hours submit on the longest option) with a whitespace-only reason trimmed to `undefined`; an already-paused scope's checkbox is disabled with an explanation; Cancel never calls the pause API; a partial multi-scope pause failure attempts every scope, keeps the dialog open, leaves only the failed scope checked, and shows both the inline per-scope error and the summary toast; the banner's three distinct render paths (empty/error/paused); the live countdown actually ticks; the countdown reaching zero triggers a real refetch rather than a client-side "resumed" assumption; Resume fires with exactly the one clicked scope.
