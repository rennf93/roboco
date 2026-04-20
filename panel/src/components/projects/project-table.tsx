"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { ExternalLink, Pencil, GitBranch, Folder, FolderX, Key, KeyRound } from "lucide-react";
import type { ProjectSummary, Team } from "@/types";
import { EditProjectDialog } from "./edit-project-dialog";

interface ProjectTableProps {
  projects: ProjectSummary[] | undefined;
  isLoading: boolean;
}

const teamLabels: Record<Team, string> = {
  board: "Board",
  main_pm: "Main PM",
  backend: "Backend",
  frontend: "Frontend",
  ux_ui: "UX/UI",
  marketing: "Marketing",
};

const teamColors: Record<Team, string> = {
  board: "bg-purple-500/10 text-purple-500 hover:bg-purple-500/20",
  main_pm: "bg-blue-500/10 text-blue-500 hover:bg-blue-500/20",
  backend: "bg-green-500/10 text-green-500 hover:bg-green-500/20",
  frontend: "bg-orange-500/10 text-orange-500 hover:bg-orange-500/20",
  ux_ui: "bg-pink-500/10 text-pink-500 hover:bg-pink-500/20",
  marketing: "bg-yellow-500/10 text-yellow-500 hover:bg-yellow-500/20",
};

function getWorkspaceBadge(hasWorkspace: boolean) {
  if (hasWorkspace) {
    return (
      <Badge className="bg-green-500/10 text-green-500">
        <Folder className="h-3 w-3 mr-1" />
        Ready
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground">
      <FolderX className="h-3 w-3 mr-1" />
      No workspace
    </Badge>
  );
}

function getTokenBadge(hasGitToken: boolean) {
  if (hasGitToken) {
    return (
      <Badge className="bg-green-500/10 text-green-500">
        <Key className="h-3 w-3 mr-1" />
        Token Set
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-amber-500 border-amber-500/30">
      <KeyRound className="h-3 w-3 mr-1" />
      No Token
    </Badge>
  );
}

export function ProjectTable({ projects, isLoading }: ProjectTableProps) {
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (!projects || projects.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <GitBranch className="h-12 w-12 mx-auto mb-4 opacity-50" />
        <p className="text-lg font-medium">No projects found</p>
        <p className="text-sm">Create a project to get started with git integration</p>
      </div>
    );
  }

  // Convert git URL to browsable URL (strip .git suffix, handle SSH format)
  const getExternalUrl = (project: ProjectSummary) => {
    let url = project.git_url;
    // Remove .git suffix
    if (url.endsWith(".git")) {
      url = url.slice(0, -4);
    }
    // Convert SSH format (git@github.com:org/repo) to HTTPS
    if (url.startsWith("git@")) {
      url = url.replace("git@", "https://").replace(":", "/");
    }
    return url;
  };

  return (
    <>
      <div className="border rounded-lg">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Project</TableHead>
              <TableHead>Cell</TableHead>
              <TableHead>Token</TableHead>
              <TableHead>Workspace</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="w-[100px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {projects.map((project) => (
              <TableRow key={project.id}>
                <TableCell>
                  <div>
                    <button
                      onClick={() => setEditingProjectId(project.id)}
                      className="font-medium hover:underline text-left"
                    >
                      {project.name}
                    </button>
                    <p className="text-xs text-muted-foreground font-mono">
                      {project.slug}
                    </p>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge className={teamColors[project.assigned_cell]}>
                    {teamLabels[project.assigned_cell]}
                  </Badge>
                </TableCell>
                <TableCell>{getTokenBadge(project.has_git_token)}</TableCell>
                <TableCell>{getWorkspaceBadge(project.has_workspace)}</TableCell>
                <TableCell>
                  {project.is_active ? (
                    <Badge className="bg-green-500/10 text-green-500">Active</Badge>
                  ) : (
                    <Badge variant="outline" className="text-muted-foreground">
                      Inactive
                    </Badge>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setEditingProjectId(project.id)}
                      title="Edit project"
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" asChild title="View repository">
                      <a
                        href={getExternalUrl(project)}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <ExternalLink className="h-4 w-4" />
                      </a>
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Edit Project Dialog */}
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
