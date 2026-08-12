"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getErrorMessage } from "@/lib/api/client";
import {
  maintenanceApi,
  type MaintenancePauseStatus,
  type MaintenanceScope,
} from "@/lib/api/maintenance";

export const maintenanceKeys = {
  all: ["maintenance"] as const,
  status: () => [...maintenanceKeys.all, "status"] as const,
};

/**
 * Polls GET /api/maintenance-pause every 30s, the same cadence the
 * connection indicator and the dashboard cards already use. A paused scope
 * must be unmissable, so this stays on a background poll rather than
 * manual-refresh-only; the navbar control and the persistent banner share
 * this one query so they never disagree.
 */
export function useMaintenanceStatus() {
  return useQuery({
    queryKey: maintenanceKeys.status(),
    queryFn: () => maintenanceApi.list(),
    refetchInterval: 30000,
  });
}

export interface PauseScopesResult {
  succeeded: MaintenancePauseStatus[];
  failed: { scope: MaintenanceScope; message: string }[];
}

/**
 * Pauses several scopes in one dialog submit even though the API is
 * one-POST-per-scope: fires every request in parallel (independent toggles,
 * no reason to serialize) and never aborts early on a single failure, since
 * an operator picking three scopes wants the other two applied regardless of
 * which one 422s. Every outcome, success or failure, is reported back to the
 * caller instead of swallowed. Regardless of outcome, the status query is
 * invalidated (not locally patched) so the cache always reflects exactly
 * what the server has, never an assumed "it must have worked" state for a
 * call that actually failed.
 */
export function usePauseMaintenanceScopes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      scopes,
      reason,
      hours,
    }: {
      scopes: MaintenanceScope[];
      reason?: string;
      hours: number;
    }): Promise<PauseScopesResult> => {
      const outcomes = await Promise.allSettled(
        scopes.map((scope) => maintenanceApi.pause(scope, { reason, hours })),
      );
      const succeeded: MaintenancePauseStatus[] = [];
      const failed: { scope: MaintenanceScope; message: string }[] = [];
      outcomes.forEach((outcome, i) => {
        const scope = scopes[i];
        if (outcome.status === "fulfilled") {
          succeeded.push(outcome.value);
        } else {
          failed.push({ scope, message: getErrorMessage(outcome.reason) });
        }
      });
      return { succeeded, failed };
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: maintenanceKeys.status() });
    },
  });
}

export function useResumeMaintenance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scope: MaintenanceScope) => maintenanceApi.resume(scope),
    onSuccess: (data: MaintenancePauseStatus) => {
      qc.setQueryData<MaintenancePauseStatus[]>(
        maintenanceKeys.status(),
        (prev) =>
          prev ? prev.map((s) => (s.scope === data.scope ? data : s)) : prev,
      );
    },
  });
}
