"use client";

import { useState } from "react";
import { useProject, useUpdateProject } from "@/hooks/use-projects";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConventionsTab } from "@/components/conventions/conventions-tab";
import { Key, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { Team, type ProjectUpdate, type Project } from "@/types";

const cells: { value: Team; label: string }[] = [
  { value: Team.BACKEND, label: "Backend" },
  { value: Team.FRONTEND, label: "Frontend" },
  { value: Team.UX_UI, label: "UX/UI" },
];

interface EditProjectDialogProps {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Inner form component - receives project directly, manages its own state
function EditProjectForm({
  project,
  onSuccess,
  onCancel,
}: {
  project: Project;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const updateProject = useUpdateProject();

  // Initialize form state from project
  const [name, setName] = useState(project.name);
  const [gitUrl, setGitUrl] = useState(project.git_url);
  const [assignedCell, setAssignedCell] = useState(project.assigned_cell);
  const [defaultBranch, setDefaultBranch] = useState(project.default_branch);
  const [isActive, setIsActive] = useState(project.is_active);
  const [testCommand, setTestCommand] = useState(project.test_command || "");
  const [lintCommand, setLintCommand] = useState(project.lint_command || "");
  const [formatCommand, setFormatCommand] = useState(
    project.format_command || "",
  );
  const [typecheckCommand, setTypecheckCommand] = useState(
    project.typecheck_command || "",
  );
  const [buildCommand, setBuildCommand] = useState(project.build_command || "");
  const [qualityCommand, setQualityCommand] = useState(
    project.quality_command || "",
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
  const sandboxServices = project.sandbox_services || [];
  const [sandboxPostgres, setSandboxPostgres] = useState(
    sandboxServices.includes("postgres"),
  );
  const [sandboxRedis, setSandboxRedis] = useState(
    sandboxServices.includes("redis"),
  );

  // Token handling
  const [newToken, setNewToken] = useState("");
  const [clearToken, setClearToken] = useState(false);

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showAutonomy, setShowAutonomy] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!name || !gitUrl || !assignedCell) {
      toast.error("Please fill in all required fields");
      return;
    }

    // Build update payload
    const updates: ProjectUpdate = {
      name,
      git_url: gitUrl,
      assigned_cell: assignedCell,
      default_branch: defaultBranch || "main",
      is_active: isActive,
      test_command: testCommand || undefined,
      lint_command: lintCommand || undefined,
      format_command: formatCommand || undefined,
      typecheck_command: typecheckCommand || undefined,
      build_command: buildCommand || undefined,
      quality_command: qualityCommand || undefined,
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
      sandbox_services: [
        ...(sandboxPostgres ? ["postgres"] : []),
        ...(sandboxRedis ? ["redis"] : []),
      ],
    };

    // Handle token update
    if (clearToken) {
      updates.git_token = ""; // Empty string clears the token
    } else if (newToken) {
      updates.git_token = newToken; // New token replaces old
    }
    // If neither, token is left unchanged (undefined)

    try {
      await updateProject.mutateAsync({ projectId: project.id, updates });
      toast.success("Project updated successfully");
      onSuccess();
    } catch (error) {
      toast.error(
        `Failed to update project: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>Edit Project</DialogTitle>
        <DialogDescription>
          Update project settings. Slug cannot be changed.
        </DialogDescription>
      </DialogHeader>
      <div className="grid gap-4 py-4">
        {/* Slug (read-only) */}
        <div className="grid gap-2">
          <Label htmlFor="slug">Slug</Label>
          <Input
            id="slug"
            value={project.slug}
            disabled
            className="font-mono text-muted-foreground"
          />
        </div>

        {/* Name */}
        <div className="grid gap-2">
          <Label htmlFor="name">Project Name *</Label>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="RoboCo API"
          />
        </div>

        {/* Git URL */}
        <div className="grid gap-2">
          <Label htmlFor="git_url">Git URL *</Label>
          <Input
            id="git_url"
            value={gitUrl}
            onChange={(e) => setGitUrl(e.target.value)}
            placeholder="https://github.com/org/repo.git"
          />
        </div>

        {/* Git Token Section */}
        <div className="grid gap-2 p-3 border rounded-lg bg-muted/30">
          <div className="flex items-center justify-between">
            <Label className="flex items-center gap-2">
              {project.has_git_token ? (
                <>
                  <Key className="h-4 w-4 text-green-500" />
                  <span className="text-green-600 dark:text-green-400">
                    Token is set
                  </span>
                </>
              ) : (
                <>
                  <KeyRound className="h-4 w-4 text-amber-500" />
                  <span className="text-amber-600 dark:text-amber-400">
                    No token configured
                  </span>
                </>
              )}
            </Label>
            {project.has_git_token && (
              <div className="flex items-center gap-2">
                <Label
                  htmlFor="clear-token"
                  className="text-xs text-muted-foreground"
                >
                  Clear token
                </Label>
                <Switch
                  id="clear-token"
                  checked={clearToken}
                  onCheckedChange={(checked) => {
                    setClearToken(checked);
                    if (checked) setNewToken("");
                  }}
                />
              </div>
            )}
          </div>

          {!clearToken && (
            <div className="grid gap-2">
              <Label htmlFor="git_token" className="text-sm">
                {project.has_git_token ? "Replace token" : "Set token"}
              </Label>
              <Input
                id="git_token"
                type="password"
                value={newToken}
                onChange={(e) => setNewToken(e.target.value)}
                placeholder="ghp_xxxxxxxxxxxx..."
              />
              <p className="text-xs text-muted-foreground">
                Personal access token with repo access for clone, push, and PR
                operations
              </p>
            </div>
          )}
        </div>

        {/* Assigned Cell */}
        <div className="grid gap-2">
          <Label htmlFor="assigned_cell">Assigned Cell *</Label>
          <Select
            value={assignedCell}
            onValueChange={(value: Team) => setAssignedCell(value)}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select cell" />
            </SelectTrigger>
            <SelectContent>
              {cells.map((cell) => (
                <SelectItem key={cell.value} value={cell.value}>
                  {cell.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Default Branch */}
        <div className="grid gap-2">
          <Label htmlFor="default_branch">Default Branch</Label>
          <Input
            id="default_branch"
            value={defaultBranch}
            onChange={(e) => setDefaultBranch(e.target.value)}
            placeholder="main"
          />
        </div>

        {/* Active Status */}
        <div className="flex items-center justify-between">
          <Label htmlFor="is_active">Active</Label>
          <Switch
            id="is_active"
            checked={isActive}
            onCheckedChange={setIsActive}
          />
        </div>

        {/* Advanced Options Toggle */}
        <Button
          type="button"
          variant="ghost"
          className="justify-start px-0 text-muted-foreground"
          onClick={() => setShowAdvanced(!showAdvanced)}
        >
          {showAdvanced ? "Hide" : "Show"} CI/CD Commands
        </Button>

        {showAdvanced && (
          <>
            <div className="grid gap-2">
              <Label htmlFor="test_command">Test Command</Label>
              <Input
                id="test_command"
                value={testCommand}
                onChange={(e) => setTestCommand(e.target.value)}
                placeholder="uv run pytest"
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="lint_command">Lint Command</Label>
              <Input
                id="lint_command"
                value={lintCommand}
                onChange={(e) => setLintCommand(e.target.value)}
                placeholder="uv run ruff check ."
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="format_command">Format Command</Label>
              <Input
                id="format_command"
                value={formatCommand}
                onChange={(e) => setFormatCommand(e.target.value)}
                placeholder="uv run ruff format ."
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="typecheck_command">Typecheck Command</Label>
              <Input
                id="typecheck_command"
                value={typecheckCommand}
                onChange={(e) => setTypecheckCommand(e.target.value)}
                placeholder="uv run mypy src/"
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="build_command">Build Command</Label>
              <Input
                id="build_command"
                value={buildCommand}
                onChange={(e) => setBuildCommand(e.target.value)}
                placeholder="pnpm build"
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="quality_command">Quality Gate Command</Label>
              <Input
                id="quality_command"
                value={qualityCommand}
                onChange={(e) => setQualityCommand(e.target.value)}
                placeholder="make gate"
              />
              <p className="text-xs text-muted-foreground">
                Fast pre-submit gate (lint + types + complexity, no tests) run
                in the dev&apos;s workspace at hand-off to QA.
              </p>
            </div>
          </>
        )}

        {/* Autonomous Maintenance Toggle */}
        <Button
          type="button"
          variant="ghost"
          className="justify-start px-0 text-muted-foreground"
          onClick={() => setShowAutonomy(!showAutonomy)}
        >
          {showAutonomy ? "Hide" : "Show"} Autonomous Maintenance
        </Button>

        {showAutonomy && (
          <>
            <div className="flex items-center justify-between">
              <Label htmlFor="ci_watch_enabled">
                CI-watch (open a fix task when CI goes red)
              </Label>
              <Switch
                id="ci_watch_enabled"
                checked={ciWatchEnabled}
                onCheckedChange={setCiWatchEnabled}
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="ci_watch_workflow">CI-watch Workflow</Label>
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
              <Label htmlFor="video_engine_enabled">
                Video engine (author marketing videos into this project)
              </Label>
              <Switch
                id="video_engine_enabled"
                checked={videoEngineEnabled}
                onCheckedChange={setVideoEngineEnabled}
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="dep_update_command">
                Dependency-Update Command
              </Label>
              <Input
                id="dep_update_command"
                value={depUpdateCommand}
                onChange={(e) => setDepUpdateCommand(e.target.value)}
                placeholder="uv lock --upgrade"
              />
              <p className="text-xs text-muted-foreground">
                Set to opt this project into the weekly dependency-update bot;
                leave blank to opt out.
              </p>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="dep_update_paths">
                Dependency-Update Lockfile Paths
              </Label>
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

            <div className="grid gap-2">
              <Label>Sandbox Services</Label>
              <div className="flex items-center justify-between">
                <Label
                  htmlFor="sandbox_postgres"
                  className="text-sm font-normal"
                >
                  PostgreSQL
                </Label>
                <Switch
                  id="sandbox_postgres"
                  checked={sandboxPostgres}
                  onCheckedChange={setSandboxPostgres}
                />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="sandbox_redis" className="text-sm font-normal">
                  Redis
                </Label>
                <Switch
                  id="sandbox_redis"
                  checked={sandboxRedis}
                  onCheckedChange={setSandboxRedis}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                Provision a throwaway sandbox DB/Redis per agent spawn for
                this project instead of the production credentials.
              </p>
            </div>
          </>
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={updateProject.isPending}>
          {updateProject.isPending ? "Saving..." : "Save Changes"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// Main dialog component - handles data fetching and dialog state
export function EditProjectDialog({
  projectId,
  open,
  onOpenChange,
}: EditProjectDialogProps) {
  const { data: project, isLoading } = useProject(projectId);
  const [tab, setTab] = useState("settings");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Settings is a compact form; Conventions uses a two-column grid, so it
          gets a wider, responsive modal (capped so it stays sane on a 27"). */}
      <DialogContent
        className={`max-h-[90vh] overflow-y-auto ${
          tab === "conventions"
            ? "sm:max-w-2xl lg:max-w-5xl xl:max-w-6xl"
            : "sm:max-w-[525px]"
        }`}
      >
        {isLoading ? (
          <div className="space-y-4 py-4">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : project ? (
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="settings">Settings</TabsTrigger>
              <TabsTrigger value="conventions">Conventions</TabsTrigger>
            </TabsList>
            <TabsContent value="settings">
              {/* Key forces remount when project changes, resetting form state */}
              <EditProjectForm
                key={project.id}
                project={project}
                onSuccess={() => onOpenChange(false)}
                onCancel={() => onOpenChange(false)}
              />
            </TabsContent>
            <TabsContent value="conventions">
              <ConventionsTab projectId={projectId} />
            </TabsContent>
          </Tabs>
        ) : (
          <div className="py-8 text-center text-muted-foreground">
            Project not found
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
