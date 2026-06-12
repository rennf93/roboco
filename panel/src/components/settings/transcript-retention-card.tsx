"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { settingsApi } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { HardDrive, Save } from "lucide-react";
import { toast } from "sonner";

const RETENTION_KEY = "transcript_retention_days";
const DEFAULT_RETENTION = "14";

export function TranscriptRetentionCard() {
  const queryClient = useQueryClient();
  const [days, setDays] = useState<string>(DEFAULT_RETENTION);

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: settingsApi.getAll,
  });

  useEffect(() => {
    const stored = settings?.[RETENTION_KEY];
    if (stored !== undefined) {
      setDays(stored);
    }
  }, [settings]);

  const saveMutation = useMutation({
    mutationFn: (value: string) => settingsApi.update(RETENTION_KEY, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      toast.success("Transcript retention updated");
    },
    onError: (error) => {
      toast.error(
        `Failed to save: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const handleSave = () => {
    const parsed = Number(days);
    if (!Number.isInteger(parsed) || parsed < 1) {
      toast.error("Retention must be a whole number of days (at least 1)");
      return;
    }
    saveMutation.mutate(String(parsed));
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HardDrive className="h-5 w-5" />
          Transcript Retention
        </CardTitle>
        <CardDescription>
          How long agent transcripts are kept before the background sweep prunes
          them. Only agent-owned transcripts are pruned — your own Claude
          sessions are never touched.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="transcript-retention-days">Retention window (days)</Label>
          <Input
            id="transcript-retention-days"
            type="number"
            min={1}
            value={days}
            disabled={isLoading}
            onChange={(e) => setDays(e.target.value)}
            className="max-w-[160px]"
          />
        </div>
        <Button onClick={handleSave} disabled={saveMutation.isPending || isLoading}>
          <Save className="h-4 w-4 mr-2" />
          {saveMutation.isPending ? "Saving..." : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}
