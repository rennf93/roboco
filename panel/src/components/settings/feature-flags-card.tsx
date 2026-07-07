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
import { useState } from "react";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { XCredentialsForm } from "@/components/settings/x-credentials-card";
import { TikTokCredentialsForm } from "@/components/settings/tiktok-credentials-card";
import { cn } from "@/lib/utils";
import { Flag, ChevronDown, ChevronRight } from "lucide-react";
import { toast } from "sonner";

// One-line blurb per flag so the operator knows what each master switch gates.
const FLAG_DESCRIPTIONS: Record<string, string> = {
  external_pr_enabled:
    "Discover and review inbound external/fork pull requests.",
  internal_pr_enabled:
    "Run the read-only safety reviewer on internal branch PRs.",
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
  rag_auto_update_enabled:
    "Keep the knowledge base index refreshed automatically.",
  transcript_prune_enabled:
    "Run the background sweep that prunes old transcripts.",
  gateway_health_enabled:
    "Recover an agent whose MCP gateway has broken (it can run no tools) while its container stays up — kill + respawn it instead of shielding it from the reaper forever.",
  ci_watch_enabled:
    "Watch every opted-in project's CI and open a fix task when its default branch goes red (per-project opt-in; never auto-merges).",
  dep_update_enabled:
    "Periodically probe opted-in projects for dependency updates and open an update task when a lockfile would change (per-project opt-in; never auto-merges).",
  release_manager_enabled:
    "Run the deterministic release-readiness sweep and propose a release for you to approve or reject — it never publishes without your approval, and the executor is fail-closed on a red gate.",
  org_memory_enabled:
    "Close the learn→reuse loop: distill a lesson at task completion, index journal reflections, and auto-inject similar past lessons + approved playbooks into an agent's briefing on claim.",
  sandbox_db_enabled:
    "Provision a throwaway Postgres/Redis sibling container per agent spawn (per-project opt-in) instead of injecting RoboCo's own production DB credentials into the agent's gate.",
  routing_strict:
    "Fail-closed model routing: refuse to silently downgrade an agent to the legacy Anthropic path when its configured provider is disabled (raise instead). Off => graceful degradation with a warning.",
  x_engine_enabled:
    "Draft release-announcement posts for the X (Twitter) account — every draft is held in a queue for you to edit, approve, or reject; nothing posts automatically, and it stays inert until you set credentials in the X card below.",
  x_replies_enabled:
    "Also poll X mentions and draft replies (still held for your approval — nothing auto-replies). Off by default: reading mentions needs a paid X API tier, so leave this off if you only want release posts.",
  x_feature_spotlight_enabled:
    "Periodically spawn the Head of Marketing to investigate RoboCo's shipped features and draft a spotlight post for an under-publicized capability — held in the same X post queue below for your approval. Needs x_engine_enabled and X credentials.",
  roadmap_engine_enabled:
    "Weekly: the Product Owner explores the company's projects and proposes a themed cycle of 3-7 roadmap items — you approve or reject each one individually; approved items land in the backlog and nothing auto-starts.",
  fable_mode_enabled:
    "Compose the Fable behavioral doctrine into every agent's system prompt and install the matching turn-discipline/honesty/verification hooks at spawn (both Claude Code and grok runtimes). Off by default; spawn path is byte-for-byte unchanged.",
  video_engine_enabled:
    "Master switch for the video-generation engine — a UX/UI dev authors a bespoke Remotion composition per trigger, then a render pass produces the 9:16/1:1 MP4 and holds it here as a draft. Even when on, distribution needs an explicit per-clip approval below; set X / TikTok credentials to post.",
  video_on_release:
    "Also open a video-authoring task when a release publishes. Off by default even with video_engine_enabled on.",
  video_on_spotlight:
    "Also open a video-authoring task when you approve a feature-spotlight draft that requests one. Off by default even with video_engine_enabled on.",
};

export function FeatureFlagsCard() {
  const queryClient = useQueryClient();
  const [xCredsOpen, setXCredsOpen] = useState(false);
  const [tiktokCredsOpen, setTiktokCredsOpen] = useState(false);
  // Off-transition awaiting operator confirm. Null = no dialog open.
  const [confirmFlag, setConfirmFlag] = useState<FeatureFlag | null>(null);
  // Every in-flight toggle key — added on mutate, removed on settle. Tracks
  // concurrent toggles so each row locks independently of the others.
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set());

  const { data, isLoading } = useQuery({
    queryKey: ["feature-flags"],
    queryFn: settingsApi.getFeatureFlags,
  });

  const toggleMutation = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      settingsApi.setFeatureFlag(key, enabled),
    onMutate: ({ key }) => {
      setPendingKeys((s) => {
        const n = new Set(s);
        n.add(key);
        return n;
      });
    },
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
    onSettled: (_data, _err, { key }) => {
      setPendingKeys((s) => {
        const n = new Set(s);
        n.delete(key);
        return n;
      });
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
      <CardContent>
        {isLoading && (
          <p className="text-sm text-muted-foreground">
            Loading feature flags…
          </p>
        )}
        {!isLoading && flags.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No feature flags available.
          </p>
        )}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {flags.map((flag) => {
            const isXEngine = flag.key === "x_engine_enabled";
            const isVideoEngine = flag.key === "video_engine_enabled";
            return (
              <div
                key={flag.key}
                className={cn(
                  "rounded-lg border p-4",
                  (isXEngine || isVideoEngine) && "md:col-span-2",
                )}
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <Label htmlFor={`flag-${flag.key}`}>{flag.label}</Label>
                    <p className="text-sm text-muted-foreground">
                      {FLAG_DESCRIPTIONS[flag.key] ?? ""}
                    </p>
                  </div>
                  <Switch
                    id={`flag-${flag.key}`}
                    checked={flag.enabled}
                    disabled={pendingKeys.has(flag.key)}
                    onCheckedChange={(checked) => {
                      // Off-transitions are destructive (a running subsystem
                      // stops on the next restart) — confirm before firing.
                      // On-transitions are low-risk and fire immediately.
                      if (checked) {
                        toggleMutation.mutate({ key: flag.key, enabled: true });
                      } else {
                        setConfirmFlag(flag);
                      }
                    }}
                  />
                </div>
                {isXEngine && (
                  <Collapsible
                    open={xCredsOpen}
                    onOpenChange={setXCredsOpen}
                    className="mt-3"
                  >
                    <CollapsibleTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="w-full justify-between px-2 text-muted-foreground"
                      >
                        <span className="text-sm">X (Twitter) credentials</span>
                        {xCredsOpen ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="pt-3">
                      <XCredentialsForm />
                    </CollapsibleContent>
                  </Collapsible>
                )}
                {isVideoEngine && (
                  <Collapsible
                    open={tiktokCredsOpen}
                    onOpenChange={setTiktokCredsOpen}
                    className="mt-3"
                  >
                    <CollapsibleTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="w-full justify-between px-2 text-muted-foreground"
                      >
                        <span className="text-sm">TikTok credentials</span>
                        {tiktokCredsOpen ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="pt-3">
                      <TikTokCredentialsForm />
                    </CollapsibleContent>
                  </Collapsible>
                )}
              </div>
            );
          })}
        </div>
      </CardContent>
      <AlertDialog
        open={confirmFlag !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmFlag(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disable feature?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmFlag
                ? `${confirmFlag.label} will turn off on the next backend restart. This may interrupt in-flight work depending on it.`
                : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const flag = confirmFlag;
                if (!flag) return;
                setConfirmFlag(null);
                toggleMutation.mutate({ key: flag.key, enabled: false });
              }}
            >
              Disable
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
