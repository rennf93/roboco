"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { RequiredNotesDialog } from "@/components/ui/required-notes-dialog";
import { getErrorMessage } from "@/lib/api/client";
import { pitchesApi, type Pitch } from "@/lib/api/pitches";

// ---------------------------------------------------------------------------
// Skeleton placeholder shaped like a PitchCard
// ---------------------------------------------------------------------------

function PitchCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-5 w-20" />
        </div>
        <div className="flex gap-1 mt-1">
          <Skeleton className="h-5 w-16" />
          <Skeleton className="h-5 w-16" />
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <Skeleton className="h-3 w-12 mb-1" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4 mt-1" />
        </div>
        <div>
          <Skeleton className="h-3 w-28 mb-1" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3 mt-1" />
        </div>
        <div className="flex gap-2">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-8 w-20" />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Individual pitch card with RequiredNotesDialog for approve/reject
// ---------------------------------------------------------------------------

interface PitchCardProps {
  pitch: Pitch;
  onApprove: (id: string, notes: string) => void;
  onReject: (id: string, notes: string) => void;
  busy: boolean;
}

function PitchCard({ pitch, onApprove, onReject, busy }: PitchCardProps) {
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const proposed = pitch.status === "proposed";

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg">{pitch.title}</CardTitle>
            <Badge variant={proposed ? "default" : "secondary"}>
              {pitch.status}
            </Badge>
          </div>
          {pitch.target_cells.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {pitch.target_cells.map((c) => (
                <Badge key={c} variant="outline">
                  {c}
                </Badge>
              ))}
            </div>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <p className="text-xs font-medium text-muted-foreground">Problem</p>
            <p className="text-sm whitespace-pre-wrap">{pitch.problem}</p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">
              Proposed solution
            </p>
            <p className="text-sm whitespace-pre-wrap">{pitch.proposed_solution}</p>
          </div>
          {proposed ? (
            <div className="flex gap-2">
              <Button
                size="sm"
                disabled={busy}
                onClick={() => setApproveOpen(true)}
              >
                <Check className="mr-1 h-4 w-4" /> Approve &amp; provision
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => setRejectOpen(true)}
              >
                <X className="mr-1 h-4 w-4" /> Reject
              </Button>
            </div>
          ) : (
            pitch.decision_notes && (
              <p className="text-xs text-muted-foreground">
                Decision: {pitch.decision_notes}
              </p>
            )
          )}
        </CardContent>
      </Card>

      {/* Approve dialog */}
      <RequiredNotesDialog
        open={approveOpen}
        onOpenChange={setApproveOpen}
        title="Approve pitch"
        description="Add a note to accompany your approval. This will be recorded in the decision log."
        notesLabel="Approval note"
        placeholder="Why are you approving this pitch?"
        submitLabel="Approve & provision"
        isPending={busy}
        onSubmit={(notes) => {
          setApproveOpen(false);
          onApprove(pitch.id, notes);
        }}
      />

      {/* Reject dialog */}
      <RequiredNotesDialog
        open={rejectOpen}
        onOpenChange={setRejectOpen}
        title="Reject pitch"
        description="Please provide a reason for rejecting this pitch."
        notesLabel="Rejection reason"
        placeholder="Why are you rejecting this pitch?"
        submitLabel="Reject"
        isPending={busy}
        onSubmit={(notes) => {
          setRejectOpen(false);
          onReject(pitch.id, notes);
        }}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

export function PitchesTab() {
  const qc = useQueryClient();

  const {
    data: pitches = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["pitches"],
    queryFn: () => pitchesApi.list(),
    refetchInterval: 30000,
  });

  const approveMutation = useMutation({
    mutationFn: ({ id, notes }: { id: string; notes: string }) =>
      pitchesApi.approve(id, notes),
    onSuccess: () => {
      toast.success("Pitch approved — provisioning started");
      void qc.invalidateQueries({ queryKey: ["pitches"] });
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, notes }: { id: string; notes: string }) =>
      pitchesApi.reject(id, notes),
    onSuccess: () => {
      toast.success("Pitch rejected");
      void qc.invalidateQueries({ queryKey: ["pitches"] });
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  const busy = approveMutation.isPending || rejectMutation.isPending;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Pitches</CardTitle>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refetch()}
            disabled={isLoading}
          >
            <RefreshCw className="mr-1 h-4 w-4" /> Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-4">
            <PitchCardSkeleton />
            <PitchCardSkeleton />
          </div>
        ) : isError ? (
          <OfflineState
            title="Failed to load pitches"
            description="Could not reach the orchestrator API. Check the backend is running."
            onRetry={() => void refetch()}
          />
        ) : pitches.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No pitches yet. The Board authors them; they appear here for your
            approval.
          </p>
        ) : (
          <div className="space-y-4">
            {pitches.map((p: Pitch) => (
              <PitchCard
                key={p.id}
                pitch={p}
                busy={busy}
                onApprove={(id, notes) => approveMutation.mutate({ id, notes })}
                onReject={(id, notes) => rejectMutation.mutate({ id, notes })}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
