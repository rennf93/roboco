import api from "./client";

// =============================================================================
// Operator maintenance pause. Real contract, mirrors
// roboco/api/routes/maintenance_pause.py exactly (CEO-only server-side; the
// panel's default identity headers in lib/api/client.ts already carry the
// CEO's X-Agent-ID/X-Agent-Role, so no extra auth wiring is needed here).
//
//   GET    /api/maintenance-pause         -> MaintenancePauseStatus[]  (always
//                                             all three scopes, one row each)
//   POST   /api/maintenance-pause/{scope} body: PauseScopeRequest
//                                             -> MaintenancePauseStatus  (the
//                                             one scope just paused)
//   DELETE /api/maintenance-pause/{scope} -> MaintenancePauseStatus  (the one
//                                             scope just resumed; idempotent)
//
// One call per scope, no batch endpoint: a dialog that lets the operator pick
// several scopes at once issues one POST per selection (see
// hooks/use-maintenance.ts's usePauseMaintenanceScopes).
// =============================================================================

export const MAINTENANCE_SCOPES = [
  "dispatch",
  "board_programs",
  "engines",
] as const;

export type MaintenanceScope = (typeof MAINTENANCE_SCOPES)[number];

export const MAINTENANCE_SCOPE_LABELS: Record<MaintenanceScope, string> = {
  dispatch: "Delivery dispatch",
  board_programs: "Board programs",
  engines: "Originating engines",
};

// Long-form, house-convention "dense tooltip" copy: one full sentence of
// what actually stops, plus the always-true "in-flight work drains" promise.
export const MAINTENANCE_SCOPE_DESCRIPTIONS: Record<MaintenanceScope, string> =
  {
    dispatch:
      "Halts new task claims and new agent spawns for the normal delivery lifecycle (dev, QA, PM, PR reviewer). Agents already mid-task keep running until they finish.",
    board_programs:
      "Halts new Board Program cycles (Printer, Pest Control, Scales, and the rest of the registry) from opening. A cycle already in flight runs to completion.",
    engines:
      "Halts every other background origination loop (self-heal, CI-watch, dependency updates, release manager, docs sync, and similar) from opening new work. Nothing in flight is affected.",
  };

/** Human label for a scope key, including one this file doesn't know about
 * yet: falls back to the raw key so the UI never renders blank. */
export function maintenanceScopeLabel(scope: string): string {
  return (
    MAINTENANCE_SCOPE_LABELS[scope as MaintenanceScope] ??
    scope.replace(/_/g, " ")
  );
}

export function maintenanceScopeDescription(scope: string): string | undefined {
  return MAINTENANCE_SCOPE_DESCRIPTIONS[scope as MaintenanceScope];
}

export interface MaintenancePauseStatus {
  scope: string;
  paused: boolean;
  /** Agent slug / CEO identity that issued the pause; null when not paused. */
  paused_by: string | null;
  /** ISO 8601 timestamp; null when not paused. */
  paused_at: string | null;
  /** Free-text reason the operator gave; null when omitted or not paused. */
  reason: string | null;
  /** ISO 8601 timestamp the pause lifts itself. Always set on a paused row,
   * the backend has no indefinite pause (hours is bounded, see
   * MAX_PAUSE_HOURS below). */
  expires_at: string | null;
  /** ISO 8601 timestamp since this scope's runtime gate (the backend's
   * `is_paused`) started reading fail-closed on a settings-store lookup
   * error, or null when its last read succeeded. Independent of `paused`:
   * a scope can read `paused: false` here (the stored setting genuinely
   * isn't paused) while this is non-null, meaning the orchestrator is
   * treating it as paused anyway until its own next lookup succeeds --
   * a phantom pause, not a human one. Optional: older cached responses
   * (and every non-list response before this field existed) may omit it. */
  read_degraded_since?: string | null;
}

export interface PauseScopeRequest {
  reason?: string;
  /** Hours the pause lasts, > 0 and <= MAX_PAUSE_HOURS. Server default 4.0
   * when omitted; the panel always sends an explicit value. */
  hours?: number;
}

// Mirrors roboco/foundation/policy/maintenance_pause.py exactly.
export const DEFAULT_PAUSE_HOURS = 4;
export const MAX_PAUSE_HOURS = 24 * 14;

/** Look up one scope's status in the list response, defaulting to "not
 * paused" when the backend hasn't reported that scope yet (or the query
 * hasn't loaded). Keeps every read-site from repeating the same
 * `.find(...) ?? {...}`. */
export function maintenanceScopeStatus(
  data: MaintenancePauseStatus[] | undefined,
  scope: string,
): MaintenancePauseStatus {
  return (
    data?.find((s) => s.scope === scope) ?? {
      scope,
      paused: false,
      paused_by: null,
      paused_at: null,
      reason: null,
      expires_at: null,
      read_degraded_since: null,
    }
  );
}

export const maintenanceApi = {
  list: async (): Promise<MaintenancePauseStatus[]> => {
    const { data } =
      await api.get<MaintenancePauseStatus[]>("/maintenance-pause");
    return data;
  },
  pause: async (
    scope: MaintenanceScope,
    body: PauseScopeRequest,
  ): Promise<MaintenancePauseStatus> => {
    const { data } = await api.post<MaintenancePauseStatus>(
      `/maintenance-pause/${scope}`,
      body,
    );
    return data;
  },
  resume: async (scope: MaintenanceScope): Promise<MaintenancePauseStatus> => {
    const { data } = await api.delete<MaintenancePauseStatus>(
      `/maintenance-pause/${scope}`,
    );
    return data;
  },
};
