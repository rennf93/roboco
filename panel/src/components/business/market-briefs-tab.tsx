"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { periscopeApi } from "@/lib/api";
import type { MarketBrief, MarketBriefFinding } from "@/lib/api/periscope";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { HelpTip } from "@/components/ui/help-tip";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { CheckCircle2, XCircle } from "lucide-react";
import { toast } from "sonner";

const _MIN_REASON_CHARS = 4;

interface RejectTarget {
  taskId: string;
  finding: MarketBriefFinding;
}

// ---------------------------------------------------------------------------
// Read-only skeleton placeholder shaped like a brief card
// ---------------------------------------------------------------------------

function BriefCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-64" />
        <Skeleton className="h-4 w-40 mt-1" />
      </CardHeader>
      <CardContent className="space-y-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </CardContent>
    </Card>
  );
}

function findingStatusBadge(finding: MarketBriefFinding) {
  if (finding.status === "approved") {
    return (
      <HelpTip label="Materialized as a Main-PM-owned task">
        <Badge variant="secondary" className="bg-green-600/10 text-green-700">
          Approved
        </Badge>
      </HelpTip>
    );
  }
  if (finding.status === "rejected") {
    return (
      <HelpTip label="Dismissed — not added to the backlog">
        <Badge variant="outline">Dismissed</Badge>
      </HelpTip>
    );
  }
  return null;
}

// ---------------------------------------------------------------------------
// One finding — claim/relevance/source, plus a per-finding approve/dismiss
// action (a market signal is worth acting on individually, even though the
// brief itself is a report).
// ---------------------------------------------------------------------------

function FindingRow({
  taskId,
  finding,
  onApprove,
  onReject,
  approving,
}: {
  taskId: string;
  finding: MarketBriefFinding;
  onApprove: (taskId: string, findingId: string) => void;
  onReject: (target: RejectTarget) => void;
  approving: boolean;
}) {
  const isProposed = finding.status === "proposed";

  return (
    <li className="space-y-1 border-b pb-3 last:border-b-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <p className="whitespace-pre-wrap text-sm">{finding.claim}</p>
        {findingStatusBadge(finding)}
      </div>
      <p className="text-xs text-muted-foreground">
        {finding.relevance} —{" "}
        <a
          href={finding.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="underline"
        >
          source
        </a>
      </p>
      {finding.status === "rejected" && finding.reject_reason && (
        <p className="text-xs text-destructive">
          Dismissed: {finding.reject_reason}
        </p>
      )}
      {isProposed && (
        <div className="flex justify-end gap-2 pt-1">
          <HelpTip label="Records your reason — not added to the backlog">
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => onReject({ taskId, finding })}
            >
              <XCircle className="mr-1 h-3.5 w-3.5" />
              Dismiss
            </Button>
          </HelpTip>
          <HelpTip label="Materializes this finding as a Main-PM-owned task — needs normal PM activation to start">
            <Button
              size="sm"
              className="bg-green-600 hover:bg-green-700"
              disabled={approving}
              onClick={() => onApprove(taskId, finding.id)}
            >
              <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
              Approve
            </Button>
          </HelpTip>
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// One brief — headline/findings/sources/threats/opportunities, each finding
// with its own approve/dismiss action.
// ---------------------------------------------------------------------------

function BriefCard({
  brief,
  onApprove,
  onReject,
  approvingFindingId,
}: {
  brief: MarketBrief;
  onApprove: (taskId: string, findingId: string) => void;
  onReject: (target: RejectTarget) => void;
  approvingFindingId: string | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">{brief.headline}</CardTitle>
        {brief.completed_at && (
          <p className="text-xs text-muted-foreground">
            Filed {new Date(brief.completed_at).toLocaleString()}
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {brief.findings.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Findings
            </p>
            <ul className="space-y-2">
              {brief.findings.map((f) => (
                <FindingRow
                  key={f.id}
                  taskId={brief.task_id}
                  finding={f}
                  onApprove={onApprove}
                  onReject={onReject}
                  approving={approvingFindingId === f.id}
                />
              ))}
            </ul>
          </div>
        )}
        {brief.threats.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Threats
            </p>
            <div className="flex flex-wrap gap-1">
              {brief.threats.map((t) => (
                <HelpTip key={t} label={t}>
                  <Badge variant="destructive" className="max-w-full truncate">
                    {t}
                  </Badge>
                </HelpTip>
              ))}
            </div>
          </div>
        )}
        {brief.opportunities.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Opportunities
            </p>
            <div className="flex flex-wrap gap-1">
              {brief.opportunities.map((o) => (
                <HelpTip key={o} label={o}>
                  <Badge
                    variant="secondary"
                    className="max-w-full truncate bg-green-600/10 text-green-700"
                  >
                    {o}
                  </Badge>
                </HelpTip>
              ))}
            </div>
          </div>
        )}
        {brief.positioning_note && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Positioning
            </p>
            <p className="text-sm whitespace-pre-wrap">
              {brief.positioning_note}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

export function MarketBriefsTab() {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState<RejectTarget | null>(null);
  const [reason, setReason] = useState("");
  const [approvingFindingId, setApprovingFindingId] = useState<string | null>(
    null,
  );

  const {
    data: briefs = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["periscope", "briefs"],
    queryFn: () => periscopeApi.listBriefs(),
    refetchInterval: 30000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["periscope", "briefs"] });

  const approveMutation = useMutation({
    mutationFn: ({
      taskId,
      findingId,
    }: {
      taskId: string;
      findingId: string;
    }) => periscopeApi.approveFinding(taskId, findingId),
    onSuccess: (result) => {
      invalidate();
      if (
        result.status === "approved" ||
        result.status === "already_approved"
      ) {
        toast.success("Finding approved — materialized as a task");
      } else {
        toast.warning(result.detail);
      }
    },
    onError: (e) =>
      toast.error(
        `Approve failed: ${e instanceof Error ? e.message : "error"}`,
      ),
    onSettled: () => setApprovingFindingId(null),
  });

  const rejectMutation = useMutation({
    mutationFn: ({
      taskId,
      findingId,
      reason,
    }: {
      taskId: string;
      findingId: string;
      reason: string;
    }) => periscopeApi.rejectFinding(taskId, findingId, reason),
    onSuccess: () => {
      invalidate();
      toast.success("Finding dismissed");
      closeReject();
    },
    onError: (e) =>
      toast.error(
        `Dismiss failed: ${e instanceof Error ? e.message : "error"}`,
      ),
  });

  const closeReject = () => {
    setRejecting(null);
    setReason("");
  };

  const confirmReject = () => {
    if (!rejecting) return;
    if (reason.trim().length < _MIN_REASON_CHARS) {
      toast.error("Give a brief reason for dismissing");
      return;
    }
    rejectMutation.mutate({
      taskId: rejecting.taskId,
      findingId: rejecting.finding.id,
      reason: reason.trim(),
    });
  };

  const handleApprove = (taskId: string, findingId: string) => {
    setApprovingFindingId(findingId);
    approveMutation.mutate({ taskId, findingId });
  };

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Market Briefs</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-4">
              <BriefCardSkeleton />
              <BriefCardSkeleton />
            </div>
          ) : isError ? (
            <OfflineState
              title="Failed to load market briefs"
              description="Could not reach the orchestrator API. Check the backend is running."
              onRetry={() => void refetch()}
            />
          ) : briefs.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No market briefs filed yet. The Head of Marketing files one
              weekly, once Periscope is enabled — each cited finding can be
              approved or dismissed individually once one lands.
            </p>
          ) : (
            <div className="space-y-4">
              {briefs.map((b) => (
                <BriefCard
                  key={b.task_id}
                  brief={b}
                  onApprove={handleApprove}
                  onReject={setRejecting}
                  approvingFindingId={approvingFindingId}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!rejecting} onOpenChange={() => closeReject()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Dismiss market-brief finding</DialogTitle>
            <DialogDescription>
              This records your reason and feeds the next cycle&apos;s prompt —
              it is not added to the backlog.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="periscope-reject-reason">Reason</Label>
            <Textarea
              id="periscope-reject-reason"
              placeholder="e.g. not actionable right now; already covered by an open task..."
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeReject}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmReject}
              disabled={rejectMutation.isPending}
            >
              {rejectMutation.isPending ? "Dismissing..." : "Dismiss"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
