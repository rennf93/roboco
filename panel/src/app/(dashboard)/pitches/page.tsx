"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { getErrorMessage } from "@/lib/api/client";
import { pitchesApi, type Pitch } from "@/lib/api/pitches";

function PitchCard({
  pitch,
  onApprove,
  onReject,
  busy,
}: {
  pitch: Pitch;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  busy: boolean;
}) {
  const proposed = pitch.status === "proposed";
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">{pitch.title}</CardTitle>
          <Badge variant={proposed ? "default" : "secondary"}>{pitch.status}</Badge>
        </div>
        <div className="flex flex-wrap gap-1">
          {pitch.target_cells.map((c) => (
            <Badge key={c} variant="outline">
              {c}
            </Badge>
          ))}
        </div>
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
            <Button size="sm" disabled={busy} onClick={() => onApprove(pitch.id)}>
              <Check className="mr-1 h-4 w-4" /> Approve &amp; provision
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => onReject(pitch.id)}
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
  );
}

export default function PitchesPage() {
  const qc = useQueryClient();

  const { data: pitches = [], isLoading } = useQuery({
    queryKey: ["pitches"],
    queryFn: () => pitchesApi.list(),
    refetchInterval: 30000,
  });

  const approveMutation = useMutation({
    mutationFn: (id: string) => pitchesApi.approve(id),
    onSuccess: () => {
      toast.success("Pitch approved — provisioning started");
      void qc.invalidateQueries({ queryKey: ["pitches"] });
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  const rejectMutation = useMutation({
    mutationFn: (id: string) => pitchesApi.reject(id, "Rejected by CEO"),
    onSuccess: () => {
      toast.success("Pitch rejected");
      void qc.invalidateQueries({ queryKey: ["pitches"] });
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  const busy = approveMutation.isPending || rejectMutation.isPending;

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Pitches</h1>
        <p className="text-muted-foreground">
          Board proposals. Approving a pitch provisions a repository per target
          cell, registers the projects, and seeds the first task to Main PM.
        </p>
      </div>
      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : pitches.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No pitches yet. The Board authors them; they appear here for your
          approval.
        </p>
      ) : (
        <div className="space-y-4">
          {pitches.map((p) => (
            <PitchCard
              key={p.id}
              pitch={p}
              busy={busy}
              onApprove={(id) => approveMutation.mutate(id)}
              onReject={(id) => rejectMutation.mutate(id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
