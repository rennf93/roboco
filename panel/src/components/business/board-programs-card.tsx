"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { HelpTip } from "@/components/ui/help-tip";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { getErrorMessage } from "@/lib/api/client";
import { boardProgramsApi, type BoardProgram } from "@/lib/api/board-programs";
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
