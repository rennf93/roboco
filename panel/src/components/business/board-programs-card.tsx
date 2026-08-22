"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { History, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { HelpTip } from "@/components/ui/help-tip";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import {
  DIALOG_SIZES,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { getErrorMessage } from "@/lib/api/client";
import {
  boardProgramsApi,
  type BoardProgram,
  type BoardProgramCycle,
  type BoardProgramDecision,
} from "@/lib/api/board-programs";
import { settingsApi } from "@/lib/api/settings";

const TRIGGER_HINTS: Record<string, string> = {
  cron: "Runs on a fixed cadence.",
  metric: "Runs when a monitored metric crosses a threshold.",
  event: "Opened only by an explicit event hook, never by the loop.",
};

function ProgramRowSkeleton() {
  return (
    <div className="rounded-lg border p-4 space-y-2">
      <Skeleton className="h-5 w-40" />
      <Skeleton className="h-4 w-64" />
    </div>
  );
}

function decisionSnapshotTitle(decision: BoardProgramDecision): string {
  const snapshotTitle = decision.item_snapshot?.title;
  return typeof snapshotTitle === "string" && snapshotTitle
    ? snapshotTitle
    : decision.item_ref;
}

function DecisionRow({ decision }: { decision: BoardProgramDecision }) {
  return (
    <div className="border-t pt-2 text-sm first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={decision.verdict === "approved" ? "default" : "secondary"}>
          {decision.verdict}
        </Badge>
        <span className="font-medium">{decisionSnapshotTitle(decision)}</span>
      </div>
      {decision.reason && (
        <p className="text-muted-foreground mt-1">{decision.reason}</p>
      )}
    </div>
  );
}

function CycleRow({ cycle }: { cycle: BoardProgramCycle }) {
  return (
    <div className="rounded-lg border p-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{new Date(cycle.opened_at).toLocaleString()}</span>
        <span>
          {cycle.items_proposed} proposed &middot; {cycle.items_approved} approved
          &middot; {cycle.items_rejected} rejected
        </span>
      </div>
      {cycle.nothing_to_propose_reason && (
        <p className="text-sm text-muted-foreground">
          Nothing to propose: {cycle.nothing_to_propose_reason}
        </p>
      )}
      {cycle.decisions.map((d, i) => (
        // Decisions are an append-only, order-stable server-rendered list;
        // no stable id exists to key on, index is safe here.
        <DecisionRow key={i} decision={d} />
      ))}
    </div>
  );
}

function ProgramHistoryDialog({ program }: { program: BoardProgram }) {
  const [open, setOpen] = useState(false);
  const {
    data: cycles = [],
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["board-program-cycles", program.key],
    queryFn: () => boardProgramsApi.cycles(program.key),
    enabled: open,
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="ghost">
          <History className="mr-1 h-4 w-4" /> History
        </Button>
      </DialogTrigger>
      <DialogContent className={DIALOG_SIZES.lg}>
        <DialogHeader>
          <DialogTitle>{program.title || program.key}: cycle history</DialogTitle>
          <DialogDescription>
            Every recorded cycle and the CEO&apos;s per-item decisions, newest
            first. Survives even after the exploration task behind an old
            cycle is deleted.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-3 overflow-y-auto">
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : isError ? (
            <p className="text-sm text-destructive">
              Failed to load history.
            </p>
          ) : cycles.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No cycles recorded yet.
            </p>
          ) : (
            cycles.map((c) => <CycleRow key={c.id} cycle={c} />)
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ProgramRow({ program }: { program: BoardProgram }) {
  const qc = useQueryClient();

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      settingsApi.setFeatureFlag(
        `board_program.${program.key}.enabled`,
        enabled,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["board-programs"] });
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  const runNowMutation = useMutation({
    mutationFn: () => boardProgramsApi.runNow(program.key),
    onSuccess: () => {
      toast.success(`${program.title || program.key} cycle opened`);
      void qc.invalidateQueries({ queryKey: ["board-programs"] });
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <HelpTip label={program.description || `The ${program.key} program.`}>
              <span className="font-medium">{program.title || program.key}</span>
            </HelpTip>
            <span className="font-mono text-xs text-muted-foreground">
              {program.key}
            </span>
            <HelpTip label={`Explored by the ${program.role} role.`}>
              <Badge variant="outline">{program.role}</Badge>
            </HelpTip>
            <HelpTip label={TRIGGER_HINTS[program.trigger] ?? ""}>
              <Badge variant="secondary">{program.trigger}</Badge>
            </HelpTip>
            <HelpTip
              label={
                program.scope === "project"
                  ? "Reads one repo — only opted-in projects feed its cycles."
                  : "Reads the org's process/market — runs org-wide by default."
              }
            >
              <Badge variant="outline">{program.scope}</Badge>
            </HelpTip>
            {program.open_cycle && (
              <HelpTip label="A cycle is already open — Run now is disabled until it closes.">
                <Badge>cycle open</Badge>
              </HelpTip>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            {program.last_opened_at
              ? `Last run: ${new Date(program.last_opened_at).toLocaleString()}`
              : "Never run"}
            {program.last_cycle_summary
              ? ` — ${program.last_cycle_summary}`
              : ""}
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <div className="flex items-center gap-2">
            <HelpTip
              label={`Toggle ${program.title || program.key} on/off. ${program.description} Persists immediately; the background loop picks it up on its next tick.`}
            >
              <Label
                htmlFor={`board-program-${program.key}`}
                className="text-xs text-muted-foreground"
              >
                Enabled
              </Label>
            </HelpTip>
            <Switch
              id={`board-program-${program.key}`}
              checked={program.enabled}
              disabled={toggleMutation.isPending}
              onCheckedChange={(checked) => toggleMutation.mutate(checked)}
            />
          </div>
          <HelpTip
            label={
              program.open_cycle
                ? "A cycle is already open for this program."
                : "Open a cycle off-schedule, ignoring the cron cadence."
            }
          >
            <span className="inline-block">
              <Button
                size="sm"
                variant="outline"
                disabled={program.open_cycle || runNowMutation.isPending}
                onClick={() => runNowMutation.mutate()}
              >
                <Play className="mr-1 h-4 w-4" /> Run now
              </Button>
            </span>
          </HelpTip>
          <ProgramHistoryDialog program={program} />
        </div>
      </div>
    </div>
  );
}

export function BoardProgramsCard() {
  const {
    data: programs = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["board-programs"],
    queryFn: () => boardProgramsApi.list(),
    refetchInterval: 30000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Board Programs</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            <ProgramRowSkeleton />
            <ProgramRowSkeleton />
          </div>
        ) : isError ? (
          <OfflineState
            title="Failed to load Board Programs"
            description="Could not reach the orchestrator API. Check the backend is running."
            onRetry={() => void refetch()}
          />
        ) : programs.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No Board Programs registered yet.
          </p>
        ) : (
          <div className="space-y-3">
            {programs.map((p) => (
              <ProgramRow key={p.key} program={p} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
