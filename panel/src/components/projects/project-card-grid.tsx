"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { ExternalLink, GitBranch, Pencil } from "lucide-react";
import type { ProjectSummary } from "@/types";
import { EditProjectDialog } from "./edit-project-dialog";
import {
  CiWatchBadge,
  TasksCell,
  getExternalUrl,
  getStatusBadge,
  getTokenBadge,
  teamColors,
  teamLabels,
} from "./project-table";

interface ProjectCardGridProps {
  projects: ProjectSummary[] | undefined;
  isLoading: boolean;
}

// Same intrinsic-sizing grid as the Agents page card grid (agent-grid.tsx):
// one column on a phone, as many as fit at 17rem+ on a wide monitor.
const GRID_COLS = "grid-cols-[repeat(auto-fill,minmax(17rem,1fr))]";

export function ProjectCardGrid({ projects, isLoading }: ProjectCardGridProps) {
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className={"grid gap-3 " + GRID_COLS}>
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i} className="gap-2.5 py-4">
            <CardHeader className="gap-1 px-4">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-3 w-16" />
            </CardHeader>
          </Card>
        ))}
      </div>
    );
  }

  if (!projects || projects.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <GitBranch className="h-12 w-12 mx-auto mb-4 opacity-50" />
        <p className="text-lg font-medium">No projects found</p>
        <p className="text-sm">
          Create a project to get started with git integration
        </p>
      </div>
    );
  }

  return (
    <>
      <div className={"grid gap-3 " + GRID_COLS}>
        {projects.map((project) => (
          <Card key={project.id} className="gap-2.5 py-4">
            <CardHeader className="gap-1 px-4">
              <div className="flex items-center justify-between gap-1">
                <CardTitle className="min-w-0 truncate text-base">
                  <Button
                    onClick={() => setEditingProjectId(project.id)}
                    variant="link"
                    className="h-auto max-w-full truncate p-0 font-semibold text-base text-foreground"
                  >
                    {project.name}
                  </Button>
                </CardTitle>
                <div className="flex shrink-0 items-center gap-0.5">
                  <HelpTip label="Edit project settings and CI/CD commands">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => setEditingProjectId(project.id)}
                      aria-label="Edit project"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                  </HelpTip>
                  <HelpTip label="Open the git repository in a new tab">
                    <Button variant="ghost" size="icon" className="h-6 w-6" asChild>
                      <a
                        href={getExternalUrl(project)}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label="View repository"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </Button>
                  </HelpTip>
                </div>
              </div>
              <HelpTip label="Composes each agent's workspace clone path and every branch name for this project.">
                <p className="w-fit truncate text-xs text-muted-foreground font-mono">
                  {project.slug}
                </p>
              </HelpTip>
            </CardHeader>
            <CardContent className="px-4 space-y-2.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <HelpTip label="Only this cell's agents can claim tasks on this project.">
                  <Badge className={teamColors[project.assigned_cell]}>
                    {teamLabels[project.assigned_cell]}
                  </Badge>
                </HelpTip>
                {getStatusBadge(project.is_active)}
                {getTokenBadge(project.has_git_token)}
                {project.ci_watch_enabled && <CiWatchBadge enabled />}
              </div>
              <TasksCell counts={project.task_counts} />
            </CardContent>
          </Card>
        ))}
      </div>

      {editingProjectId && (
        <EditProjectDialog
          projectId={editingProjectId}
          open={!!editingProjectId}
          onOpenChange={(open) => {
            if (!open) setEditingProjectId(null);
          }}
        />
      )}
    </>
  );
}
