"use client";

import { useState } from "react";
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
import { HelpTip } from "@/components/ui/help-tip";
import { HardDrive, Save } from "lucide-react";
import { toast } from "sonner";

const RETENTION_KEY = "transcript_retention_days";
const DEFAULT_RETENTION = "14";

export function TranscriptRetentionCard() {
  const queryClient = useQueryClient();
  // `edited` holds the user's in-progress input; null means "show the server
  // value". Deriving the displayed value avoids syncing query state into local
  // state with an effect (react-hooks/set-state-in-effect).
  const [edited, setEdited] = useState<string | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: settingsApi.getAll,
  });

  const serverValue = settings?.[RETENTION_KEY] ?? DEFAULT_RETENTION;
  const days = edited ?? serverValue;

  const saveMutation = useMutation({
    mutationFn: (value: string) => settingsApi.update(RETENTION_KEY, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      setEdited(null); // re-sync the input to the freshly-saved server value
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
          <HelpTip label="Only takes effect while the transcript_prune_enabled feature flag is on.">
            <Label htmlFor="transcript-retention-days">
              Retention window (days)
            </Label>
          </HelpTip>
          <Input
            id="transcript-retention-days"
            type="number"
            min={1}
            value={days}
            disabled={isLoading}
            onChange={(e) => setEdited(e.target.value)}
            className="max-w-[160px]"
          />
        </div>
        <Button
          onClick={handleSave}
          disabled={saveMutation.isPending || isLoading}
        >
          <Save className="h-4 w-4 mr-2" />
          {saveMutation.isPending ? "Saving..." : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}
