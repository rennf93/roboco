"use client";

import { useState } from "react";
import { useUpdateProject } from "@/hooks/use-projects";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { HelpTip } from "@/components/ui/help-tip";
import { Wallet } from "lucide-react";
import { toast } from "sonner";
import type { Project, ProjectUpdate } from "@/types";
import { SaveBar } from "./save-bar";

export function BudgetOpsCard({ project }: { project: Project }) {
  const updateProject = useUpdateProject();

  const [monthlyBudgetUsd, setMonthlyBudgetUsd] = useState(
    project.monthly_budget_usd != null
      ? String(project.monthly_budget_usd)
      : "",
  );
  const [ciWatchEnabled, setCiWatchEnabled] = useState(
    project.ci_watch_enabled,
  );
  const [ciWatchWorkflow, setCiWatchWorkflow] = useState(
    project.ci_watch_workflow || "",
  );
  const [videoEngineEnabled, setVideoEngineEnabled] = useState(
    project.video_engine_enabled,
  );
  const [depUpdateCommand, setDepUpdateCommand] = useState(
    project.dep_update_command || "",
  );
  const [depUpdatePaths, setDepUpdatePaths] = useState(
    (project.dep_update_paths || []).join(", "),
  );

  const dirty =
    monthlyBudgetUsd !==
      (project.monthly_budget_usd != null
        ? String(project.monthly_budget_usd)
        : "") ||
    ciWatchEnabled !== project.ci_watch_enabled ||
    ciWatchWorkflow !== (project.ci_watch_workflow || "") ||
    videoEngineEnabled !== project.video_engine_enabled ||
    depUpdateCommand !== (project.dep_update_command || "") ||
    depUpdatePaths !== (project.dep_update_paths || []).join(", ");

  const handleSave = async () => {
    const trimmedBudget = monthlyBudgetUsd.trim();
    const parsedBudget = trimmedBudget ? Number(trimmedBudget) : null;
    if (trimmedBudget && (Number.isNaN(parsedBudget) || parsedBudget! <= 0)) {
      toast.error(
        "Monthly budget must be greater than 0 — leave it empty for no cap",
      );
      return;
    }

    const updates: ProjectUpdate = {
      // Sent explicitly (never coerced to undefined) so clearing the input
      // actually clears the stored cap instead of being dropped.
      monthly_budget_usd: parsedBudget,
      ci_watch_enabled: ciWatchEnabled,
      ci_watch_workflow: ciWatchWorkflow || undefined,
      video_engine_enabled: videoEngineEnabled,
      dep_update_command: depUpdateCommand || undefined,
      dep_update_paths: depUpdatePaths.trim()
        ? depUpdatePaths
            .split(",")
            .map((p) => p.trim())
            .filter(Boolean)
        : undefined,
    };

    try {
      await updateProject.mutateAsync({ projectId: project.id, updates });
      toast.success("Budget & ops updated");
    } catch (error) {
      toast.error(
        `Failed to update: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Wallet className="h-5 w-5" />
          Budget & Ops
        </CardTitle>
        <CardDescription>
          Spend cap and autonomous-maintenance opt-ins
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2">
          <HelpTip label="Calendar-month cap on this project's summed agent-spawn spend; a claim is refused once reached. Requires the task-budgets flag armed fleet-wide (ROBOCO_TASK_BUDGETS_ENABLED). Leave blank for no cap.">
            <Label htmlFor="monthly_budget_usd">Monthly Budget (USD)</Label>
          </HelpTip>
          <Input
            id="monthly_budget_usd"
            type="number"
            min="0.01"
            step="0.01"
            value={monthlyBudgetUsd}
            onChange={(e) => setMonthlyBudgetUsd(e.target.value)}
            placeholder="No cap"
          />
          <p className="text-xs text-muted-foreground">
            Claims are refused once this month&apos;s spend reaches the cap.
            Must be greater than 0 — a 0 budget would block every claim
            immediately. Leave blank for no cap.
          </p>
          {project.monthly_spend_usd != null && (
            <p
              className="text-xs text-muted-foreground"
              data-testid="project-spend"
            >
              Spent: ${project.monthly_spend_usd.toFixed(2)} this month
              {monthlyBudgetUsd.trim() &&
              !Number.isNaN(Number(monthlyBudgetUsd))
                ? ` / $${Number(monthlyBudgetUsd).toFixed(2)}`
                : ""}
            </p>
          )}
        </div>

        <div className="flex items-center justify-between">
          <HelpTip label="Opens a fix task automatically when this repo's default-branch CI goes red. Also requires the CI-watch engine armed fleet-wide (ROBOCO_CI_WATCH_ENABLED) to actually run.">
            <Label htmlFor="ci_watch_enabled">
              CI-watch (open a fix task when CI goes red)
            </Label>
          </HelpTip>
          <Switch
            id="ci_watch_enabled"
            checked={ciWatchEnabled}
            onCheckedChange={setCiWatchEnabled}
          />
        </div>

        <div className="grid gap-2">
          <HelpTip label="Scopes CI-watch to one workflow's runs so a green run elsewhere can't mask a red one here; leave blank to fall back to the fleet default (ci.yml).">
            <Label htmlFor="ci_watch_workflow">CI-watch Workflow</Label>
          </HelpTip>
          <Input
            id="ci_watch_workflow"
            value={ciWatchWorkflow}
            onChange={(e) => setCiWatchWorkflow(e.target.value)}
            placeholder="ci.yml"
          />
          <p className="text-xs text-muted-foreground">
            Workflow file to scope the CI signal to. Leave blank to use the
            engine default.
          </p>
        </div>

        <div className="flex items-center justify-between">
          <HelpTip label="Opts this repo into authoring motion-graphics videos under motion/. Also requires the video engine armed fleet-wide (ROBOCO_VIDEO_ENGINE_ENABLED) to render/post.">
            <Label htmlFor="video_engine_enabled">
              Video engine (author marketing videos into this project)
            </Label>
          </HelpTip>
          <Switch
            id="video_engine_enabled"
            checked={videoEngineEnabled}
            onCheckedChange={setVideoEngineEnabled}
          />
        </div>

        <div className="grid gap-2">
          <HelpTip label="Dry-run only — the weekly bot runs this in a throwaway clone to detect a lockfile diff; nothing is committed until it opens a task that rides the normal PR-review flow.">
            <Label htmlFor="dep_update_command">
              Dependency-Update Command
            </Label>
          </HelpTip>
          <Input
            id="dep_update_command"
            value={depUpdateCommand}
            onChange={(e) => setDepUpdateCommand(e.target.value)}
            placeholder="uv lock --upgrade"
          />
          <p className="text-xs text-muted-foreground">
            Set to opt this project into the weekly dependency-update bot; leave
            blank to opt out.
          </p>
        </div>

        <div className="grid gap-2">
          <HelpTip label="Which lockfile paths the dry-run diffs to detect a change; leave blank to auto-infer uv.lock / pnpm-lock.yaml.">
            <Label htmlFor="dep_update_paths">
              Dependency-Update Lockfile Paths
            </Label>
          </HelpTip>
          <Input
            id="dep_update_paths"
            value={depUpdatePaths}
            onChange={(e) => setDepUpdatePaths(e.target.value)}
            placeholder="uv.lock, pnpm-lock.yaml"
          />
          <p className="text-xs text-muted-foreground">
            Comma-separated lockfile paths to watch. Leave blank to infer
            uv.lock / pnpm-lock.yaml.
          </p>
        </div>

        <SaveBar
          dirty={dirty}
          pending={updateProject.isPending}
          onSave={handleSave}
        />
      </CardContent>
    </Card>
  );
}
