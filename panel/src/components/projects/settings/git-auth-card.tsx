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
import { Key, KeyRound } from "lucide-react";
import { toast } from "sonner";
import type { Project, ProjectUpdate } from "@/types";
import { SaveBar } from "./save-bar";

export function GitAuthCard({ project }: { project: Project }) {
  const updateProject = useUpdateProject();

  const [newToken, setNewToken] = useState("");
  const [clearToken, setClearToken] = useState(false);

  // This card doesn't own the GitHub App binding (Identity card does), so the
  // "no credentials at all" check reads the project's own persisted binding
  // state rather than an in-progress draft in that other card.
  const willHaveNoToken =
    clearToken || (!project.has_git_token && !newToken.trim());
  const bothAuthSourcesEmpty =
    project.github_installation_id === null && willHaveNoToken;

  const dirty = clearToken || newToken.trim().length > 0;

  const handleSave = async () => {
    const updates: ProjectUpdate = {};
    if (clearToken) {
      updates.git_token = ""; // Empty string clears the token
    } else if (newToken) {
      updates.git_token = newToken; // New token replaces old
    }

    try {
      await updateProject.mutateAsync({ projectId: project.id, updates });
      setNewToken("");
      setClearToken(false);
      toast.success(clearToken ? "Git token cleared" : "Git token updated");
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
          <KeyRound className="h-5 w-5" />
          Git Auth
        </CardTitle>
        <CardDescription>
          Personal access token for clone, push, and PR operations
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
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

        {bothAuthSourcesEmpty && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            Saving now leaves this project with no git credentials at all — no
            GitHub App binding and no personal access token. Clone, push, and PR
            operations will fail until one is set.
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
