"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { OctagonPause, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { formatAbsoluteTimestamp } from "@/lib/utils";
import { getErrorMessage } from "@/lib/api/client";
import {
  maintenanceScopeDescription,
  maintenanceScopeLabel,
  type MaintenancePauseStatus,
  type MaintenanceScope,
} from "@/lib/api/maintenance";
import {
  useMaintenanceStatus,
  useResumeMaintenance,
} from "@/hooks/use-maintenance";

function computeSecondsLeft(expiresAt: string): number {
  return Math.max(
    0,
    Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 1000),
  );
}

function formatCountdown(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

// =============================================================================
// One row per paused scope, mirrors RateLimitBanner's per-provider row, the
// established codebase idiom for "list of live conditions with a countdown".
// =============================================================================

function MaintenanceRow({
  entry,
  onResume,
  onExpire,
  resuming,
}: {
  entry: MaintenancePauseStatus;
  onResume: (scope: string) => void;
  onExpire: () => void;
  resuming: boolean;
}) {
  const expiresAt = entry.expires_at;
  const [secondsLeft, setSecondsLeft] = useState(() =>
    expiresAt ? computeSecondsLeft(expiresAt) : null,
  );
  // Ref, not a dependency, so a re-render that hands us a new onExpire
  // identity (react-query's refetch is not guaranteed referentially stable)
  // never restarts the ticking interval below. Written in an effect, not
  // during render, refs are for event handlers/effects only.
  const onExpireRef = useRef(onExpire);
  useEffect(() => {
    onExpireRef.current = onExpire;
  });

  useEffect(() => {
    if (!expiresAt) return;
    // Tick every second; re-arms when expiresAt changes (the scope got
    // re-paused with a new duration). `paused` self-resolves server-side
    // (past expires_at reads back false with no sweep), so the local
    // countdown hitting zero must not itself flip the UI to "resumed", it
    // asks the server once instead, the single source of truth.
    let firedExpiry = false;
    const id = setInterval(() => {
      const left = computeSecondsLeft(expiresAt);
      setSecondsLeft(left);
      if (left <= 0 && !firedExpiry) {
        firedExpiry = true;
        onExpireRef.current();
      }
    }, 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  const label = maintenanceScopeLabel(entry.scope);

  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-2 bg-red-100 border-b border-red-400 last:border-b-0 dark:bg-red-950/60 dark:border-red-800">
      <OctagonPause
        className="h-4 w-4 text-red-700 dark:text-red-400 shrink-0"
        aria-hidden="true"
      />
      <HelpTip label={maintenanceScopeDescription(entry.scope)}>
        <span className="text-sm font-semibold text-red-950 dark:text-red-100">
          {label}
        </span>
      </HelpTip>
      <HelpTip label="Who issued this pause, from the panel's CEO identity.">
        <span className="text-sm text-red-900 dark:text-red-200">
          paused by {entry.paused_by ?? "unknown"}
        </span>
      </HelpTip>
      {entry.paused_at && (
        <HelpTip label="When this pause was issued.">
          <span className="text-sm text-red-800 dark:text-red-300">
            {formatAbsoluteTimestamp(entry.paused_at)}
          </span>
        </HelpTip>
      )}
      {entry.reason && (
        <HelpTip label={`Reason given: ${entry.reason}`}>
          <span className="text-sm text-red-800 dark:text-red-300 italic truncate max-w-xs">
            &quot;{entry.reason}&quot;
          </span>
        </HelpTip>
      )}
      <HelpTip label="This pause lifts itself automatically at this countdown, no action needed if it's forgotten.">
        <span className="text-sm font-medium text-red-950 dark:text-red-100 ml-auto">
          {secondsLeft !== null
            ? `auto-resumes in ${formatCountdown(secondsLeft)}`
            : "auto-resumes shortly"}
        </span>
      </HelpTip>
      <Button
        size="sm"
        variant="outline"
        aria-label={`Resume ${label}`}
        className="border-red-500 text-red-950 hover:bg-red-200 dark:text-red-100 dark:hover:bg-red-900"
        onClick={() => onResume(entry.scope)}
        disabled={resuming}
      >
        Resume
      </Button>
    </div>
  );
}

/**
 * A scope's stored state says "not paused", but its runtime gate
 * (backend `is_paused`) is CURRENTLY failing its own lookup and treating
 * the scope as paused until a later read succeeds -- not a human pause,
 * nothing to resume here, just a live settings-store read fault made
 * visible instead of silently agreeing with "not paused".
 */
function MaintenanceDegradedRow({ entry }: { entry: MaintenancePauseStatus }) {
  const label = maintenanceScopeLabel(entry.scope);
  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-1.5 bg-amber-100 border-b border-amber-400 last:border-b-0 dark:bg-amber-950/60 dark:border-amber-800">
      <TriangleAlert
        className="h-3.5 w-3.5 text-amber-700 dark:text-amber-400 shrink-0"
        aria-hidden="true"
      />
      <HelpTip label="The stored setting says not paused, but this scope's runtime gate is currently failing its lookup and treating it as paused until a read succeeds again. Not a human-issued pause -- nothing to resume, this clears itself.">
        <span className="text-xs text-amber-900 dark:text-amber-200">
          {label}: read degraded
          {entry.read_degraded_since &&
            ` since ${formatAbsoluteTimestamp(entry.read_degraded_since)}`}
          , new spawns may be treated as paused
        </span>
      </HelpTip>
    </div>
  );
}

/**
 * Persistent, high-contrast bar mounted directly below the Header (see
 * (dashboard)/layout.tsx), deliberately not a subtle badge, since a pause
 * nobody notices is a silent outage. Renders nothing when nothing is paused
 * and no scope is read-degraded (the correct "empty" state, mirroring
 * RateLimitBanner); a status-fetch error renders a distinct, slimmer amber
 * strip instead of silently agreeing with "not paused", since that would be
 * worse than admitting the state is unknown.
 */
export function MaintenanceBanner() {
  const { data, isError, error, refetch } = useMaintenanceStatus();
  const resumeMutation = useResumeMaintenance();

  const handleResume = (scope: string) => {
    const label = maintenanceScopeLabel(scope);
    resumeMutation.mutate(scope as MaintenanceScope, {
      onSuccess: () => {
        toast.success(`Resumed ${label}`);
      },
      onError: (err) => {
        toast.error(`Failed to resume ${label}: ${getErrorMessage(err)}`);
      },
    });
  };

  // A row's local countdown hitting zero never assumes the scope is
  // resumed on its own, it refetches so the banner never disagrees with the
  // server's own self-resolved `paused` read.
  const handleExpire = () => {
    void refetch();
  };

  if (isError) {
    return (
      <div
        className="flex items-center gap-2 px-4 py-1.5 border-b border-amber-300 bg-amber-50 dark:bg-amber-950/40 dark:border-amber-900"
        role="alert"
      >
        <TriangleAlert
          className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400 shrink-0"
          aria-hidden="true"
        />
        <span className="text-xs text-amber-800 dark:text-amber-300">
          Maintenance-pause status unavailable ({getErrorMessage(error)}).
          Whether spawns are paused is currently unknown.
        </span>
      </div>
    );
  }

  const pausedScopes = (data ?? []).filter((s) => s.paused);
  // Distinct from a human pause -- see MaintenanceDegradedRow above.
  const degradedScopes = (data ?? []).filter(
    (s) => !s.paused && s.read_degraded_since,
  );

  if (pausedScopes.length === 0 && degradedScopes.length === 0) {
    return null;
  }

  return (
    <>
      {degradedScopes.length > 0 && (
        <div role="alert" aria-label="Maintenance pause read degraded">
          {degradedScopes.map((entry) => (
            <MaintenanceDegradedRow key={entry.scope} entry={entry} />
          ))}
        </div>
      )}
      {pausedScopes.length > 0 && (
        <div
          className="border-b border-red-400 bg-red-100 dark:border-red-800 dark:bg-red-950/60"
          role="alert"
          aria-live="polite"
          aria-label="Maintenance pause active"
        >
          {pausedScopes.map((entry) => (
            <MaintenanceRow
              key={entry.scope}
              entry={entry}
              onResume={handleResume}
              onExpire={handleExpire}
              resuming={resumeMutation.isPending}
            />
          ))}
        </div>
      )}
    </>
  );
}
