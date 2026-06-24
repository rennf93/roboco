"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { settingsApi } from "@/lib/api";
import type { FeatureFlag } from "@/lib/api/settings";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Flag } from "lucide-react";
import { toast } from "sonner";

// One-line blurb per flag so the operator knows what each master switch gates.
const FLAG_DESCRIPTIONS: Record<string, string> = {
  external_pr_enabled: "Discover and review inbound external/fork pull requests.",
  internal_pr_enabled: "Run the read-only safety reviewer on internal branch PRs.",
  research_enabled: "Let the Board and PMs run web research.",
  strategy_engine_enabled: "Generate and maintain company strategy artifacts.",
  self_heal_enabled: "Watch RoboCo's own CI and notify you when it regresses.",
  self_heal_originate_enabled:
    "Also open a PENDING fix task for a detected regression (needs Self-healing on; the task waits for your approval).",
  provisioning_enabled: "Auto-provision projects from approved pitches.",
  toolchain_match_enabled:
    "Provision each agent workspace with the target project's Python (not RoboCo's) and block delivery gates when its test suite can't be executed.",
  conventions_enabled:
    "Enforce a per-project architectural standard (.roboco/conventions.yml): inject the map, attach baseline constraints, and block i_am_done / pr_pass on misplaced definitions or lint suppressions.",
  rag_auto_update_enabled: "Keep the knowledge base index refreshed automatically.",
  transcript_prune_enabled: "Run the background sweep that prunes old transcripts.",
  gateway_health_enabled:
    "Recover an agent whose MCP gateway has broken (it can run no tools) while its container stays up — kill + respawn it instead of shielding it from the reaper forever.",
};

export function FeatureFlagsCard() {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["feature-flags"],
    queryFn: settingsApi.getFeatureFlags,
  });

  const toggleMutation = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      settingsApi.setFeatureFlag(key, enabled),
    onSuccess: (_data, { enabled }) => {
      queryClient.invalidateQueries({ queryKey: ["feature-flags"] });
      toast.success(
        `Feature ${enabled ? "enabled" : "disabled"} — takes effect on next restart`,
      );
    },
    onError: (error) => {
      toast.error(
        `Failed to update: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const flags: FeatureFlag[] = data?.flags ?? [];
  const note = data?.note ?? "Changes take effect on the next backend restart.";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Flag className="h-5 w-5" />
          Feature Flags
        </CardTitle>
        <CardDescription>
          Master switches for optional subsystems. Unset flags fall back to the
          environment default. {note}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading feature flags…</p>
        )}
        {!isLoading && flags.length === 0 && (
          <p className="text-sm text-muted-foreground">No feature flags available.</p>
        )}
        {flags.map((flag, i) => (
          <div key={flag.key}>
            {i > 0 && <Separator className="mb-4" />}
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <Label htmlFor={`flag-${flag.key}`}>{flag.label}</Label>
                <p className="text-sm text-muted-foreground">
                  {FLAG_DESCRIPTIONS[flag.key] ?? ""}
                </p>
              </div>
              <Switch
                id={`flag-${flag.key}`}
                checked={flag.enabled}
                disabled={toggleMutation.isPending}
                onCheckedChange={(checked) =>
                  toggleMutation.mutate({ key: flag.key, enabled: checked })
                }
              />
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
