"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { releaseApi } from "@/lib/api";
import type { ReleaseExecuteResult } from "@/lib/api/release";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import { CheckCircle2, XCircle, Rocket, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { usePageRefresh } from "@/hooks";
import { HelpTip } from "@/components/ui/help-tip";

const _MIN_REJECT_CHARS = 10;

function gateBadgeVariant(
  gate: string,
): "default" | "secondary" | "destructive" | "outline" {
  if (gate === "green") return "default";
  if (gate === "red") return "destructive";
  return "secondary";
}

// A red gate / open gaps make publishing risky — the CEO should resolve them
// first. Approval still runs the fail-closed executor, so it can't ship a bad
// release; this only steers the CEO.
export function ReleaseProposalCard({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const [action, setAction] = useState<"approve" | "reject" | null>(null);
  const [requiredChanges, setRequiredChanges] = useState("");

  const {
    data: proposal,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["release", "proposal"],
    queryFn: () => releaseApi.getProposal(),
    refetchInterval: 30000,
  });

  const { register, unregister } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  const approveMutation = useMutation({
    mutationFn: () => releaseApi.approve(),
    onSuccess: (result: ReleaseExecuteResult) => {
      queryClient.invalidateQueries({ queryKey: ["release", "proposal"] });
      if (result.status === "published") {
        toast.success(
          `Published v${result.version}` +
            (result.release_url ? "" : " (no release URL returned)"),
        );
      } else if (result.status === "accepted") {
        // The execute runs in the background (a synchronous request would 504
        // at nginx before the ~40min fail-closed gate/CI/publish finished).
        // This card polls GET /proposal every 30s and reflects the final
        // outcome (COMPLETED on a publish, else the proposal stays open).
        toast.info(
          "Release execute dispatched — running in the background. This card updates as it progresses.",
        );
      } else {
        toast.warning(`Release halted (${result.status}): ${result.detail}`);
      }
      closeDialog();
    },
    onError: (error) => {
      toast.error(
        `Approve failed: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const rejectMutation = useMutation({
    mutationFn: (changes: string) => releaseApi.reject(changes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["release", "proposal"] });
      toast.success("Proposal sent back with required changes");
      closeDialog();
    },
    onError: (error) => {
      toast.error(
        `Reject failed: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const closeDialog = () => {
    setAction(null);
    setRequiredChanges("");
  };

  const handleConfirm = () => {
    if (action === "approve") {
      approveMutation.mutate();
    } else if (action === "reject") {
      if (requiredChanges.trim().length < _MIN_REJECT_CHARS) {
        toast.error("Describe the required changes (≥ 10 characters)");
        return;
      }
      rejectMutation.mutate(requiredChanges.trim());
    }
  };

  // Loading: nothing to render yet (mirrors the prior hide).
  if (isLoading) return null;
  // A genuine query failure (non-404) must NOT hide silently — a 500 / network
  // drop rethrows out of releaseApi.getProposal, leaving data undefined + isError
  // set. Collapsing that onto `!proposal` returned null, so the CEO had no idea
  // the release-proposal endpoint was unreachable. Surface it with a retry.
  // (A 404 — "no open proposal" — is mapped to null in getProposal and falls
  // through to the `!proposal` hide below, the normal empty state.)
  if (isError) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Rocket className="h-5 w-5" />
            Release Proposal
          </CardTitle>
          <CardDescription>
            Couldn&apos;t load the release proposal
            {error instanceof Error ? `: ${error.message}` : ""}.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }
  // No open proposal (404 → null) — the normal empty state, hidden (mirrors
  // PrReviewQueue).
  if (!proposal) return null;

  const { report } = proposal;
  const pending = approveMutation.isPending || rejectMutation.isPending;

  return (
    <>
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Rocket className="h-5 w-5" />
            Release Proposal
            <Badge variant="outline">v{report.proposed_version}</Badge>
            <HelpTip label="Semver bump type — how the version number increases (major, minor, or patch)">
              <Badge variant="secondary">{report.bump_kind}</Badge>
            </HelpTip>
            <HelpTip label="Quality gate status — green means all checks pass, red means failures must be fixed before release">
              <Badge variant={gateBadgeVariant(report.gate_state)}>
                gate: {report.gate_state}
              </Badge>
            </HelpTip>
          </CardTitle>
          <CardDescription>
            {report.change_summary.length} change(s) since the last tag · review
            and approve to cut the release (nothing publishes until you do).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {report.gaps.length > 0 && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
              <p className="flex items-center gap-1.5 text-sm font-medium text-amber-600">
                <AlertTriangle className="h-4 w-4" />
                {report.gaps.length} gap(s) to resolve before publishing
              </p>
              <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                {report.gaps.map((gap, i) => (
                  <li key={`${gap.category}-${i}`}>
                    <span className="font-mono text-xs uppercase">
                      [{gap.category}]
                    </span>{" "}
                    {gap.detail}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Drafted CHANGELOG
            </p>
            <pre className="max-h-60 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap">
              {report.drafted_changelog}
            </pre>
          </div>

          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Version bump plan ({report.version_bump_plan.length} files)
            </p>
            <p className="text-sm text-muted-foreground">
              {report.version_bump_plan.join(", ")}
            </p>
          </div>

          {report.migration_notes.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Migrations
              </p>
              <ul className="space-y-1 text-sm text-muted-foreground">
                {report.migration_notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            </div>
          )}

          {proposal.required_changes && (
            <p className="text-sm text-amber-600">
              Awaiting revision — you requested: {proposal.required_changes}
            </p>
          )}

          <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:items-center sm:justify-end">
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => setAction("reject")}
            >
              <XCircle className="mr-1 h-4 w-4" />
              Reject with changes
            </Button>
            <Button
              size="sm"
              className="bg-green-600 hover:bg-green-700"
              onClick={() => setAction("approve")}
            >
              <CheckCircle2 className="mr-1 h-4 w-4" />
              Approve &amp; publish
            </Button>
          </div>
        </CardContent>
      </Card>

      <Dialog open={!!action} onOpenChange={() => closeDialog()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {action === "approve"
                ? `Approve release v${report.proposed_version}?`
                : "Reject with required changes"}
            </DialogTitle>
            <DialogDescription>
              {action === "approve"
                ? "This runs the fail-closed executor: write the bumps + CHANGELOG, run make quality, commit, wait for green CI, then publish. It aborts on a red gate or red CI."
                : "Record what must change. The proposal stays open for revision; nothing is published."}
            </DialogDescription>
          </DialogHeader>

          {action === "reject" && (
            <div className="space-y-2">
              <Label htmlFor="required-changes">Required changes</Label>
              <Textarea
                id="required-changes"
                placeholder="e.g. tighten the CHANGELOG wording for the API change; hold for the migration fix..."
                value={requiredChanges}
                onChange={(e) => setRequiredChanges(e.target.value)}
                rows={3}
              />
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={closeDialog} disabled={pending}>
              Cancel
            </Button>
            <Button
              onClick={handleConfirm}
              disabled={pending}
              variant={action === "reject" ? "destructive" : "default"}
              className={
                action === "approve" ? "bg-green-600 hover:bg-green-700" : ""
              }
            >
              {pending
                ? "Processing..."
                : action === "approve"
                  ? "Approve & publish"
                  : "Send back for revision"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
