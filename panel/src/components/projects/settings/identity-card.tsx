"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { IdCard } from "lucide-react";
import { toast } from "sonner";
import type { Project, ProjectUpdate } from "@/types";
import { githubAppApi } from "@/lib/api";
import {
  SelectRepoDialog,
  type SelectedRepo,
} from "@/components/projects/select-repo-picker";
import { SaveBar } from "./save-bar";

// Auto-detect from the git URL host, mirroring
// roboco/foundation/policy/forge.py's detect_provider: only gitlab.com is
// auto-detected as non-GitHub for App-binding purposes — github.com and any
// unrecognized/self-hosted host stay github-ish (a self-hosted forge must
// set the Forge select explicitly to change this).
function isAutoDetectedGitlab(gitUrl: string): boolean {
  const url = gitUrl.trim();
  let host: string | null = null;
  if (url.includes("://")) {
    try {
      host = new URL(url).hostname.toLowerCase() || null;
    } catch {
      host = null;
    }
  } else {
    // scp-like SSH syntax: [user@]host:path
    const match = /^(?:[^@/]+@)?([^/:]+):/.exec(url);
    host = match ? match[1].toLowerCase() : null;
  }
  return host === "gitlab.com";
}

export function IdentityCard({ project }: { project: Project }) {
  const updateProject = useUpdateProject();

  const [name, setName] = useState(project.name);
  const [gitUrl, setGitUrl] = useState(project.git_url);
  const [gitProvider, setGitProvider] = useState(
    project.git_provider ?? "auto",
  );
  // Set via the "Select repo" picker (binds to a GitHub App installation) or
  // cleared via "Unbind"; null = git ops fall back to the token card's PAT.
  const [githubInstallationId, setGithubInstallationId] = useState<
    number | null
  >(project.github_installation_id);

  const { data: credStatus } = useQuery({
    queryKey: ["github-app", "credentials"],
    queryFn: () => githubAppApi.getCredentialsStatus(),
  });
  const appConfigured = !!credStatus?.has_credentials;
  const isNonGithubProvider =
    gitProvider === "gitea" ||
    gitProvider === "gitlab" ||
    (gitProvider === "auto" && isAutoDetectedGitlab(gitUrl));

  // This card doesn't own the git-token fields (Git Auth card does), so the
  // "no credentials at all" check reads the project's own persisted token
  // state rather than an in-progress draft in that other card.
  const bothAuthSourcesEmpty =
    githubInstallationId === null && !project.has_git_token;

  const dirty =
    name !== project.name ||
    gitUrl !== project.git_url ||
    gitProvider !== (project.git_provider ?? "auto") ||
    githubInstallationId !== project.github_installation_id;

  const handleRepoSelected = (repo: SelectedRepo) => {
    setGitUrl(repo.git_url);
    setGithubInstallationId(repo.installation_id);
  };

  const handleSave = async () => {
    if (!name.trim() || !gitUrl.trim()) {
      toast.error("Name and Git URL are required");
      return;
    }

    const updates: ProjectUpdate = {
      name,
      git_url: gitUrl,
      git_provider: gitProvider === "auto" ? null : gitProvider,
      // Sent explicitly (never coerced to undefined) so an unbind (null)
      // actually clears the stored installation instead of being dropped.
      github_installation_id: githubInstallationId,
    };

    try {
      await updateProject.mutateAsync({ projectId: project.id, updates });
      toast.success("Identity updated");
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
          <IdCard className="h-5 w-5" />
          Identity
        </CardTitle>
        <CardDescription>Name, repository, and forge routing</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
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

        <div className="grid gap-2">
          <HelpTip label="Cloned into each assigned agent's workspace; use HTTPS so the Git Auth card's token can authenticate clone, push, and PR operations.">
            <Label htmlFor="git_url">Git URL *</Label>
          </HelpTip>
          <Input
            id="git_url"
            value={gitUrl}
            onChange={(e) => setGitUrl(e.target.value)}
            placeholder="https://github.com/org/repo.git"
          />
        </div>

        <div className="grid gap-2">
          <HelpTip label="Which forge API serves PR/CI/review operations. Auto-detect resolves from the Git URL's host — but only at creation time, so changing the Git URL's host here needs an explicit provider re-pick (github.com -> GitHub, gitlab.com -> GitLab); a self-hosted Gitea/GitLab instance or GitHub Enterprise can't be told apart by host alone and must always be set explicitly.">
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
              <SelectItem value="gitlab">
                GitLab (gitlab.com / self-hosted)
              </SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-2 p-3 border rounded-lg bg-muted/30">
          <HelpTip label="Binding routes commits and PR reviews through a short-lived GitHub App installation token attributed to the App bot, instead of this project's personal access token; unbinding reverts to the PAT. Requires the App to be installed on this repository.">
            <Label>GitHub App</Label>
          </HelpTip>
          {!appConfigured ? (
            <p className="text-xs text-muted-foreground">
              Git operations use this project&apos;s personal access token (Git
              Auth card below). Configure the GitHub App on the Settings page to
              enable App-token (bot-attributed) auth.
            </p>
          ) : isNonGithubProvider ? (
            <p className="text-xs text-muted-foreground">
              App auth is GitHub-only — this project&apos;s forge is{" "}
              {gitProvider}, so git operations use its token below.
            </p>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm">
                {githubInstallationId !== null ? (
                  <span className="text-green-600 dark:text-green-400">
                    Using GitHub App (installation #{githubInstallationId})
                  </span>
                ) : (
                  <span className="text-muted-foreground">
                    Using personal access token
                  </span>
                )}
              </p>
              <div className="flex items-center gap-2">
                <SelectRepoDialog onSelect={handleRepoSelected} />
                {githubInstallationId !== null && (
                  <HelpTip label="Clears the installation binding on save; git operations fall back to the personal access token below.">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setGithubInstallationId(null)}
                    >
                      Unbind
                    </Button>
                  </HelpTip>
                )}
              </div>
            </div>
          )}
        </div>

        {bothAuthSourcesEmpty && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            Saving now leaves this project with no GitHub App binding and no
            personal access token stored — set one below before saving, or
            clone/push/PR operations will fail.
          </p>
        )}

        <SaveBar
          dirty={dirty}
          pending={updateProject.isPending}
          onSave={handleSave}
        />
      </CardContent>
    </Card>
  );
}
