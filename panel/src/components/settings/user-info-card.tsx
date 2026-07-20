"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { settingsApi } from "@/lib/api";
import { CEO_NAME_KEY, DEFAULT_CEO_NAME } from "@/lib/api/settings";
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
import { User, Save } from "lucide-react";
import { toast } from "sonner";

const MAX_NAME_LENGTH = 60;

export function UserInfoCard() {
  const queryClient = useQueryClient();
  // `edited` holds the in-progress input; null means "show the server value"
  // (same pattern as TranscriptRetentionCard — avoids syncing query state
  // into local state with an effect).
  const [edited, setEdited] = useState<string | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: settingsApi.getAll,
  });

  const serverValue = settings?.[CEO_NAME_KEY] ?? DEFAULT_CEO_NAME;
  const name = edited ?? serverValue;

  const saveMutation = useMutation({
    mutationFn: (value: string) => settingsApi.update(CEO_NAME_KEY, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      setEdited(null);
      toast.success("Name updated");
    },
    onError: (error) => {
      toast.error(
        `Failed to save: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const handleSave = () => {
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error("Name can't be empty");
      return;
    }
    saveMutation.mutate(trimmed);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <User className="h-5 w-5" />
          User Info
        </CardTitle>
        <CardDescription>Your account information</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-4">
          <div className="h-16 w-16 shrink-0 rounded-full bg-primary flex items-center justify-center">
            <span className="text-primary-foreground font-bold text-2xl">
              CEO
            </span>
          </div>
          <div className="flex-1 space-y-2">
            <HelpTip label="Shown in the header's user chip and this card. Doesn't change your git/legal identity — that stays with the repo's copyright owner.">
              <Label htmlFor="ceo-name">Display name</Label>
            </HelpTip>
            <div className="flex items-center gap-2">
              <Input
                id="ceo-name"
                value={name}
                disabled={isLoading}
                onChange={(e) => setEdited(e.target.value)}
                maxLength={MAX_NAME_LENGTH}
                className="max-w-[200px]"
              />
              <Button
                size="sm"
                onClick={handleSave}
                disabled={saveMutation.isPending || isLoading}
              >
                <Save className="h-4 w-4 mr-2" />
                {saveMutation.isPending ? "Saving..." : "Save"}
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">
              Chief Executive Officer
            </p>
            <HelpTip label="The CEO's fixed agent id — used to attribute your notifications, notes, and approvals across the API.">
              <p className="text-xs text-muted-foreground w-fit">
                Agent ID: 00000000-0000-0000-0000-000000000001
              </p>
            </HelpTip>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
