"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Loader2, OctagonPause, TriangleAlert, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { HelpTip } from "@/components/ui/help-tip";
import { getErrorMessage } from "@/lib/api/client";
import {
  DEFAULT_PAUSE_HOURS,
  MAINTENANCE_SCOPES,
  MAX_PAUSE_HOURS,
  maintenanceScopeDescription,
  maintenanceScopeLabel,
  maintenanceScopeStatus,
  type MaintenanceScope,
} from "@/lib/api/maintenance";
import {
  useMaintenanceStatus,
  usePauseMaintenanceScopes,
} from "@/hooks/use-maintenance";

const CONTROL_LABEL = "Operator maintenance pause";

// Fixed duration choices, hours (the wire unit). The backend has no
// indefinite pause: `hours` must be > 0 and <= MAX_PAUSE_HOURS, so the
// longest option is labeled as the real maximum instead of implying an
// unbounded pause that doesn't exist server-side.
const DURATION_OPTIONS: { value: string; label: string; hours: number }[] = [
  { value: "0.5", label: "30 minutes", hours: 0.5 },
  { value: "1", label: "1 hour", hours: 1 },
  { value: "2", label: "2 hours", hours: 2 },
  { value: "4", label: "4 hours", hours: 4 },
  {
    value: String(MAX_PAUSE_HOURS),
    label: "14 days (maximum)",
    hours: MAX_PAUSE_HOURS,
  },
];
const DEFAULT_DURATION = String(DEFAULT_PAUSE_HOURS);

/**
 * Navbar icon button (next to the connection indicator) that opens the pause
 * dialog. The icon/tooltip reflect the shared maintenance-status query so
 * this control and the MaintenanceBanner never disagree. Resuming lives
 * entirely in the banner, this control only ever pauses.
 */
export function MaintenanceControl() {
  const { data, isLoading, isError, error } = useMaintenanceStatus();
  const pauseMutation = usePauseMaintenanceScopes();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("");
  const [duration, setDuration] = useState(DEFAULT_DURATION);
  // Per-scope error from the last submit's partial failures, cleared on a
  // fresh submit or when the dialog is closed. A scope stays selected (for
  // an easy retry) exactly while it has an entry here.
  const [failedScopes, setFailedScopes] = useState<Record<string, string>>({});

  const pausedCount = useMemo(
    () => (data ?? []).filter((s) => s.paused).length,
    [data],
  );
  // Only known once the status query has resolved at least once, while
  // loading, a scope's "already paused" checkbox state can't be trusted yet.
  const statusKnown = !isLoading || data !== undefined;
  const allKnownScopesPaused =
    statusKnown &&
    MAINTENANCE_SCOPES.every(
      (scope) => maintenanceScopeStatus(data, scope).paused,
    );

  const resetForm = () => {
    setSelected(new Set());
    setReason("");
    setDuration(DEFAULT_DURATION);
    setFailedScopes({});
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) resetForm();
  };

  const toggleScope = (scope: string, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(scope);
      else next.delete(scope);
      return next;
    });
  };

  // Multiple scopes, one POST each: every request is attempted (an operator
  // picking three scopes wants the other two applied even if one 422s), and
  // the mutation's onSettled always invalidates the status query, so the
  // cache is refreshed from the server rather than assumed. A scope that
  // fails stays selected and shows its own error inline so the operator can
  // retry it alone; a scope that succeeds gets refetched as paused and its
  // checkbox disables itself.
  const handlePause = () => {
    if (selected.size === 0 || pauseMutation.isPending) return;
    const opt = DURATION_OPTIONS.find((o) => o.value === duration);
    const scopes = Array.from(selected) as MaintenanceScope[];
    setFailedScopes({});
    pauseMutation.mutate(
      {
        scopes,
        reason: reason.trim() ? reason.trim() : undefined,
        hours: opt?.hours ?? DEFAULT_PAUSE_HOURS,
      },
      {
        onSuccess: (result) => {
          if (result.failed.length === 0) {
            toast.success(
              `Paused ${scopes.length} scope${scopes.length !== 1 ? "s" : ""}. New spawns halt immediately, agents already running keep going until they finish.`,
            );
            handleOpenChange(false);
            return;
          }
          setFailedScopes(
            Object.fromEntries(result.failed.map((f) => [f.scope, f.message])),
          );
          setSelected(new Set(result.failed.map((f) => f.scope)));
          const failureSummary = result.failed
            .map((f) => `${maintenanceScopeLabel(f.scope)}: ${f.message}`)
            .join("; ");
          if (result.succeeded.length > 0) {
            toast.warning(
              `Paused ${result.succeeded.length} of ${scopes.length} scopes. ${failureSummary}`,
            );
          } else {
            toast.error(`Failed to pause: ${failureSummary}`);
          }
        },
      },
    );
  };

  let icon = <Wrench className="h-5 w-5 text-muted-foreground" />;
  let tooltip = "No maintenance pause active. Click to pause agent spawns.";
  if (isLoading && data === undefined) {
    icon = <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />;
    tooltip = "Checking maintenance-pause status...";
  } else if (isError) {
    icon = <TriangleAlert className="h-5 w-5 text-amber-600" />;
    tooltip = `Maintenance-pause status unavailable: ${getErrorMessage(error)}. Click to open the pause control anyway.`;
  } else if (pausedCount > 0) {
    icon = <OctagonPause className="h-5 w-5 text-red-600" />;
    tooltip = `${pausedCount} scope${pausedCount !== 1 ? "s" : ""} paused. See the banner below, or click to pause more.`;
  }

  const controlLabel =
    pausedCount > 0
      ? `${CONTROL_LABEL} (${pausedCount} paused)`
      : CONTROL_LABEL;

  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={controlLabel}
            onClick={() => setOpen(true)}
          >
            {icon}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{tooltip}</TooltipContent>
      </Tooltip>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Pause agent spawns</DialogTitle>
            <DialogDescription>
              Halts NEW task claims and NEW agent spawns for the scopes you
              pick. Agents already running finish their current work, nothing in
              flight is killed.
            </DialogDescription>
          </DialogHeader>

          {isError && (
            <p
              className="text-sm text-amber-600 dark:text-amber-400"
              role="alert"
            >
              Current status could not be loaded ({getErrorMessage(error)}). You
              can still pause below, but the checkboxes may not reflect what is
              really live right now.
            </p>
          )}

          {!statusKnown && (
            <div className="space-y-2" data-testid="maintenance-scopes-loading">
              <Skeleton className="h-5 w-48" />
              <Skeleton className="h-5 w-56" />
              <Skeleton className="h-5 w-40" />
            </div>
          )}

          {statusKnown && allKnownScopesPaused && (
            <p className="text-sm text-muted-foreground">
              Every scope is already paused. Resume one from the banner below
              the navbar, then come back here to pause it again if needed.
            </p>
          )}

          {statusKnown && (
            <div className="space-y-3">
              {MAINTENANCE_SCOPES.map((scope) => {
                const status = maintenanceScopeStatus(data, scope);
                return (
                  <div key={scope} className="flex items-start gap-3">
                    <Checkbox
                      id={`maintenance-scope-${scope}`}
                      checked={selected.has(scope)}
                      disabled={status.paused || pauseMutation.isPending}
                      onCheckedChange={(checked) =>
                        toggleScope(scope, checked === true)
                      }
                    />
                    <div className="min-w-0">
                      <HelpTip label={maintenanceScopeDescription(scope)}>
                        <Label htmlFor={`maintenance-scope-${scope}`}>
                          {maintenanceScopeLabel(scope)}
                        </Label>
                      </HelpTip>
                      {status.paused && (
                        <p className="text-xs text-red-600 dark:text-red-400">
                          Already paused by {status.paused_by ?? "unknown"}.
                          Resume it from the banner first.
                        </p>
                      )}
                      {failedScopes[scope] && (
                        <p
                          className="text-xs text-red-600 dark:text-red-400"
                          role="alert"
                        >
                          Failed to pause: {failedScopes[scope]}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="maintenance-duration">Auto-resume after</Label>
            <Select value={duration} onValueChange={setDuration}>
              <SelectTrigger id="maintenance-duration" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DURATION_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="maintenance-reason">Reason (optional)</Label>
            <Textarea
              id="maintenance-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. NAS migration window"
              rows={3}
              disabled={pauseMutation.isPending}
            />
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={pauseMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handlePause}
              disabled={selected.size === 0 || pauseMutation.isPending}
            >
              {pauseMutation.isPending
                ? "Pausing..."
                : selected.size > 0
                  ? `Pause ${selected.size} scope${selected.size !== 1 ? "s" : ""}`
                  : "Pause selected scopes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
