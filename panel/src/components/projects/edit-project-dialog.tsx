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
import { EnvironmentLadderEditor } from "@/components/projects/environment-ladder-editor";
import { validateLadder } from "@/components/projects/ladder-validation";
import { HelpTip } from "@/components/ui/help-tip";

const cells: { value: Team; label: string }[] = [
  { value: Team.BACKEND, label: "Backend" },
  { value: Team.FRONTEND, label: "Frontend" },
  { value: Team.UX_UI, label: "UX/UI" },
];

const SANDBOX_SERVICES = [
  { id: "postgres", label: "PostgreSQL" },
  { id: "redis", label: "Redis" },
  { id: "mongo", label: "MongoDB" },
] as const;

// Activatable extensions/modules per service, mirroring the backend allowlist
// (roboco/models/sandbox.py SANDBOX_ENGINE_FEATURES). The allowlist is the
// security containment — a plpython3u (superuser-RCE) is absent by design.
// Mongo has no activatable features and is intentionally absent here.
const SANDBOX_EXTENSIONS: Record<
  string,
  { id: string; label: string; hint: string }[]
> = {
  postgres: [
    {
      id: "vector",
      label: "pgvector",
      hint: "Vector similarity search/indexing for embeddings.",
    },
    {
      id: "postgis",
      label: "PostGIS",
      hint: "Geospatial types and queries for PostgreSQL.",
    },
    {
      id: "pg_trgm",
      label: "pg_trgm",
      hint: "Trigram-based fuzzy text matching and similarity search.",
    },
    {
      id: "citext",
      label: "citext",
      hint: "Case-insensitive text column type.",
    },
    {
      id: "uuid-ossp",
      label: "uuid-ossp",
      hint: "Functions to generate UUIDs (e.g. uuid_generate_v4()).",
    },
  ],
  redis: [
    {
      id: "search",
      label: "RediSearch",
      hint: "Full-text search and secondary indexing for Redis.",
    },
    {
      id: "json",
      label: "RedisJSON",
      hint: "Native JSON document storage and querying.",
    },
    {
      id: "bloom",
      label: "RedisBloom",
      hint: "Probabilistic data structures (Bloom/Cuckoo filters, HyperLogLog).",
    },
  ],
};

const SANDBOX_SERVICE_HINTS: Record<string, string> = {
  postgres:
    "Ephemeral PostgreSQL container for this project's agent spawns — random creds, tmpfs storage, torn down at end of engagement.",
  redis:
    "Ephemeral Redis container for this project's agent spawns — random creds, tmpfs storage, torn down at end of engagement.",
  mongo:
    "Ephemeral MongoDB container for this project's agent spawns — random creds, tmpfs storage, torn down at end of engagement.",
};

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
  const [gitProvider, setGitProvider] = useState(project.git_provider ?? "auto");
  const [assignedCell, setAssignedCell] = useState(project.assigned_cell);
  const [defaultBranch, setDefaultBranch] = useState(project.default_branch);
  const [environments, setEnvironments] = useState(project.environments ?? null);
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
  const [sandboxSet, setSandboxSet] = useState<Set<string>>(
    new Set(sandboxServices),
  );
  // Per-service extension picks (only meaningful for services in sandboxSet).
  const [sandboxExtensions, setSandboxExtensions] = useState<
    Record<string, Set<string>>
  >(() => {
    const init: Record<string, Set<string>> = {};
    for (const [svc, feats] of Object.entries(
      project.sandbox_extensions || {},
    )) {
      init[svc] = new Set(feats);
    }
    return init;
  });
  const toggleExtension = (svc: string, feat: string, checked: boolean) => {
    setSandboxExtensions((prev) => {
      const next = { ...prev };
      const set = new Set(next[svc] ?? []);
      if (checked) set.add(feat);
      else set.delete(feat);
      next[svc] = set;
      return next;
    });
  };

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

    const envError = validateLadder(environments);
    if (envError) {
      toast.error(envError);
      return;
    }

    // Build update payload
    const updates: ProjectUpdate = {
      name,
      git_url: gitUrl,
      git_provider: gitProvider === "auto" ? null : gitProvider,
      assigned_cell: assignedCell,
      default_branch: defaultBranch || "main",
      environments,
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
      sandbox_services: [...sandboxSet],
      sandbox_extensions: (() => {
        const extObj: Record<string, string[]> = {};
        for (const svc of sandboxSet) {
          const feats = sandboxExtensions[svc];
          if (feats && feats.size > 0) extObj[svc] = [...feats].sort();
        }
        return extObj;
      })(),
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
          <HelpTip label="Immutable — composes each agent's workspace clone path and appears in every branch name for this project. Set at creation, fixed here.">
            <Label htmlFor="slug">Slug</Label>
          </HelpTip>
          <Input
            id="slug"
            value={project.slug}
            disabled
            className="font-mono text-muted-foreground"
          />
        </div>

        {/* Name */}
        <div className="grid gap-2">
          <HelpTip label="Display name shown across the panel and CEO approval queues; renaming it never touches the slug or workspace path.">
            <Label htmlFor="name">Project Name *</Label>
          </HelpTip>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="RoboCo API"
          />
        </div>

        {/* Git URL */}
        <div className="grid gap-2">
          <HelpTip label="Cloned into each assigned agent's workspace; use HTTPS so the token below can authenticate clone, push, and PR operations.">
            <Label htmlFor="git_url">Git URL *</Label>
          </HelpTip>
          <Input
            id="git_url"
            value={gitUrl}
            onChange={(e) => setGitUrl(e.target.value)}
            placeholder="https://github.com/org/repo.git"
          />
        </div>

        {/* Forge provider */}
        <div className="grid gap-2">
          <HelpTip label="Which forge API serves PR/CI/review operations. Auto-detect covers github.com; a self-hosted Gitea instance (or GitHub Enterprise) must be set explicitly — the host comes from the Git URL. GitLab support is planned.">
            <Label>Forge</Label>
          </HelpTip>
          <Select value={gitProvider} onValueChange={setGitProvider}>
            <SelectTrigger>
              <SelectValue placeholder="Auto-detect" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Auto-detect (github.com)</SelectItem>
              <SelectItem value="github">GitHub / GitHub Enterprise</SelectItem>
              <SelectItem value="gitea">Gitea (self-hosted)</SelectItem>
              <SelectItem value="gitlab">GitLab (gitlab.com / self-hosted)</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Git Token Section */}
        <div className="grid gap-2 p-3 border rounded-lg bg-muted/30">
          <div className="flex items-center justify-between">
            <HelpTip label="Stored encrypted (Fernet) and never re-displayed once saved — required for HTTPS clone/push/PR operations.">
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
            </HelpTip>
            {project.has_git_token && (
              <div className="flex items-center gap-2">
                <HelpTip label="Clears the stored token when you save. Leave off to keep the current token, or enter a replacement below.">
                  <Label
                    htmlFor="clear-token"
                    className="text-xs text-muted-foreground"
                  >
                    Clear token
                  </Label>
                </HelpTip>
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
              <HelpTip
                label={
                  project.has_git_token
                    ? "Overwrites the current token immediately on save; the previous token is discarded and cannot be recovered."
                    : "Required for HTTPS clone/push/PR operations if the repo is private; stored Fernet-encrypted and never re-displayed once saved."
                }
              >
                <Label htmlFor="git_token" className="text-sm">
                  {project.has_git_token ? "Replace token" : "Set token"}
                </Label>
              </HelpTip>
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
          <HelpTip label="Which cell owns this project — only that cell's agents can claim its tasks (enforced server-side, not just a UI filter).">
            <Label htmlFor="assigned_cell">Assigned Cell *</Label>
          </HelpTip>
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
          <HelpTip label="Used as both head and prod when no environment ladder is set below (a degenerate single-rung ladder) — the PR review gate diffs against it and releases cut from it.">
            <Label htmlFor="default_branch">Default Branch</Label>
          </HelpTip>
          <Input
            id="default_branch"
            value={defaultBranch}
            onChange={(e) => setDefaultBranch(e.target.value)}
            placeholder="main"
          />
          <p className="text-xs text-muted-foreground">
            Where PRs land and releases are cut when no environment ladder is set below.
          </p>
        </div>

        {/* Environment ladder */}
        <EnvironmentLadderEditor
          rungs={environments}
          onChange={setEnvironments}
        />

        {/* Active Status */}
        <div className="flex items-center justify-between">
          <HelpTip label="Inactive projects are hidden from the default project list (toggle 'Show Inactive' to see them) and are skipped as the fallback project for idle-agent spawns.">
            <Label htmlFor="is_active">Active</Label>
          </HelpTip>
          <Switch
            id="is_active"
            checked={isActive}
            onCheckedChange={setIsActive}
          />
        </div>

        {/* Advanced Options Toggle */}
        <HelpTip label="Test/Format/Build are reference-only today; Lint + Typecheck (or Quality Gate below, which replaces both) run automatically at the dev's pre-submit gate.">
          <Button
            type="button"
            variant="ghost"
            className="justify-start px-0 text-muted-foreground"
            onClick={() => setShowAdvanced(!showAdvanced)}
          >
            {showAdvanced ? "Hide" : "Show"} CI/CD Commands
          </Button>
        </HelpTip>

        {showAdvanced && (
          <>
            <div className="grid gap-2">
              <HelpTip label="Reference only — not yet wired into any automated gate or CI run by RoboCo itself.">
                <Label htmlFor="test_command">Test Command</Label>
              </HelpTip>
              <Input
                id="test_command"
                value={testCommand}
                onChange={(e) => setTestCommand(e.target.value)}
                placeholder="uv run pytest"
              />
            </div>

            <div className="grid gap-2">
              <HelpTip label="Runs at the dev's pre-submit gate (i_am_done) alongside Typecheck — unless Quality Gate Command below is set, which replaces both.">
                <Label htmlFor="lint_command">Lint Command</Label>
              </HelpTip>
              <Input
                id="lint_command"
                value={lintCommand}
                onChange={(e) => setLintCommand(e.target.value)}
                placeholder="uv run ruff check ."
              />
            </div>

            <div className="grid gap-2">
              <HelpTip label="Reference only — deliberately excluded from the automated gate since formatting mutates files.">
                <Label htmlFor="format_command">Format Command</Label>
              </HelpTip>
              <Input
                id="format_command"
                value={formatCommand}
                onChange={(e) => setFormatCommand(e.target.value)}
                placeholder="uv run ruff format ."
              />
            </div>

            <div className="grid gap-2">
              <HelpTip label="Runs at the dev's pre-submit gate (i_am_done) alongside Lint — unless Quality Gate Command below is set, which replaces both.">
                <Label htmlFor="typecheck_command">Typecheck Command</Label>
              </HelpTip>
              <Input
                id="typecheck_command"
                value={typecheckCommand}
                onChange={(e) => setTypecheckCommand(e.target.value)}
                placeholder="uv run mypy src/"
              />
            </div>

            <div className="grid gap-2">
              <HelpTip label="Reference only — not run automatically; the slow build/test suite is left to CI.">
                <Label htmlFor="build_command">Build Command</Label>
              </HelpTip>
              <Input
                id="build_command"
                value={buildCommand}
                onChange={(e) => setBuildCommand(e.target.value)}
                placeholder="pnpm build"
              />
            </div>

            <div className="grid gap-2">
              <HelpTip label="When set, replaces the Lint + Typecheck pair as the dev's complete pre-submit gate command.">
                <Label htmlFor="quality_command">Quality Gate Command</Label>
              </HelpTip>
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
        <HelpTip label="CI-watch, video engine, dependency-update bot, and sandbox DB/Redis/Mongo opt-ins — each also needs its own fleet-wide flag armed to actually run.">
          <Button
            type="button"
            variant="ghost"
            className="justify-start px-0 text-muted-foreground"
            onClick={() => setShowAutonomy(!showAutonomy)}
          >
            {showAutonomy ? "Hide" : "Show"} Autonomous Maintenance
          </Button>
        </HelpTip>

        {showAutonomy && (
          <>
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
                Set to opt this project into the weekly dependency-update bot;
                leave blank to opt out.
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

            <div className="grid gap-2">
              <HelpTip label="Requires the sandbox engine armed fleet-wide (ROBOCO_SANDBOX_DB_ENABLED); agents call request_sandbox() on-demand rather than getting creds at spawn.">
                <Label>Sandbox Services</Label>
              </HelpTip>
              {SANDBOX_SERVICES.map((svc) => (
                <div key={svc.id} className="flex items-center justify-between">
                  <HelpTip label={SANDBOX_SERVICE_HINTS[svc.id]}>
                    <Label
                      htmlFor={`sandbox_${svc.id}`}
                      className="text-sm font-normal"
                    >
                      {svc.label}
                    </Label>
                  </HelpTip>
                  <Switch
                    id={`sandbox_${svc.id}`}
                    checked={sandboxSet.has(svc.id)}
                    onCheckedChange={(checked) =>
                      setSandboxSet((prev) => {
                        const next = new Set(prev);
                        if (checked) next.add(svc.id);
                        else next.delete(svc.id);
                        return next;
                      })
                    }
                  />
                </div>
              ))}
              <p className="text-xs text-muted-foreground">
                Provision a throwaway sandbox DB/Redis per agent spawn for this
                project instead of the production credentials.
              </p>
            </div>

            {SANDBOX_SERVICES.filter(
              (svc) => sandboxSet.has(svc.id) && SANDBOX_EXTENSIONS[svc.id],
            ).map((svc) => (
              <div key={`ext_${svc.id}`} className="grid gap-2">
                <Label>{svc.label} Extensions</Label>
                {SANDBOX_EXTENSIONS[svc.id].map((ext) => (
                  <div
                    key={ext.id}
                    className="flex items-center justify-between"
                  >
                    <HelpTip label={ext.hint}>
                      <Label
                        htmlFor={`ext_${svc.id}_${ext.id}`}
                        className="text-sm font-normal"
                      >
                        {ext.label}
                      </Label>
                    </HelpTip>
                    <Switch
                      id={`ext_${svc.id}_${ext.id}`}
                      checked={sandboxExtensions[svc.id]?.has(ext.id) ?? false}
                      onCheckedChange={(checked) =>
                        toggleExtension(svc.id, ext.id, checked)
                      }
                    />
                  </div>
                ))}
                <p className="text-xs text-muted-foreground">
                  Activated on-demand in the sandbox {svc.label} container. Set
                  the full set here so agents can request subsets.
                </p>
              </div>
            ))}
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
