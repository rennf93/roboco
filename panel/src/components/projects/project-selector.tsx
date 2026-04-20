"use client";

import { useMemo } from "react";
import { useProjects } from "@/hooks/use-projects";
import { Team } from "@/types";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { FolderGit2, X } from "lucide-react";

interface ProjectSelectorProps {
  value: string | null;
  onChange: (value: string | null) => void;
  placeholder?: string;
  filterByTeam?: Team;
  disabled?: boolean;
  allowClear?: boolean;
}

// Team display names for project cells
const TEAM_LABELS: Partial<Record<Team, string>> = {
  [Team.BACKEND]: "Backend",
  [Team.FRONTEND]: "Frontend",
  [Team.UX_UI]: "UX/UI",
};

export function ProjectSelector({
  value,
  onChange,
  placeholder = "Select project...",
  filterByTeam,
  disabled = false,
  allowClear = true,
}: ProjectSelectorProps) {
  const { data: projects = [], isLoading } = useProjects();

  // Group projects by team/cell
  const groupedProjects = useMemo(() => {
    let filtered = projects;

    // Apply team filter
    if (filterByTeam) {
      filtered = filtered.filter((p) => p.assigned_cell === filterByTeam);
    }

    // Group by team/cell
    const groups: Record<string, typeof filtered> = {
      backend: [],
      frontend: [],
      ux_ui: [],
      other: [],
    };

    for (const project of filtered) {
      if (project.assigned_cell === Team.BACKEND) {
        groups.backend.push(project);
      } else if (project.assigned_cell === Team.FRONTEND) {
        groups.frontend.push(project);
      } else if (project.assigned_cell === Team.UX_UI) {
        groups.ux_ui.push(project);
      } else {
        groups.other.push(project);
      }
    }

    return groups;
  }, [projects, filterByTeam]);

  // Find selected project for display
  const selectedProject = useMemo(() => {
    if (!value) return null;
    return projects.find((p) => p.id === value);
  }, [projects, value]);

  const handleValueChange = (newValue: string) => {
    if (newValue === "__clear__") {
      onChange(null);
    } else {
      onChange(newValue);
    }
  };

  return (
    <Select
      value={value ?? ""}
      onValueChange={handleValueChange}
      disabled={disabled || isLoading}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder}>
          {selectedProject ? (
            <div className="flex items-center gap-2">
              <FolderGit2 className="h-4 w-4" />
              <span>{selectedProject.name}</span>
              {selectedProject.assigned_cell && (
                <Badge variant="outline" className="text-xs">
                  {TEAM_LABELS[selectedProject.assigned_cell] || selectedProject.assigned_cell}
                </Badge>
              )}
            </div>
          ) : (
            placeholder
          )}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {allowClear && value && (
          <SelectItem value="__clear__" className="text-muted-foreground">
            <span className="flex items-center gap-2">
              <X className="h-4 w-4" />
              No project
            </span>
          </SelectItem>
        )}

        {/* Backend */}
        {groupedProjects.backend.length > 0 && (
          <SelectGroup>
            <SelectLabel>Backend</SelectLabel>
            {groupedProjects.backend.map((project) => (
              <SelectItem key={project.id} value={project.id}>
                <div className="flex items-center gap-2">
                  <FolderGit2 className="h-4 w-4" />
                  <span>{project.name}</span>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Frontend */}
        {groupedProjects.frontend.length > 0 && (
          <SelectGroup>
            <SelectLabel>Frontend</SelectLabel>
            {groupedProjects.frontend.map((project) => (
              <SelectItem key={project.id} value={project.id}>
                <div className="flex items-center gap-2">
                  <FolderGit2 className="h-4 w-4" />
                  <span>{project.name}</span>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* UX/UI */}
        {groupedProjects.ux_ui.length > 0 && (
          <SelectGroup>
            <SelectLabel>UX/UI</SelectLabel>
            {groupedProjects.ux_ui.map((project) => (
              <SelectItem key={project.id} value={project.id}>
                <div className="flex items-center gap-2">
                  <FolderGit2 className="h-4 w-4" />
                  <span>{project.name}</span>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Other */}
        {groupedProjects.other.length > 0 && (
          <SelectGroup>
            <SelectLabel>Other</SelectLabel>
            {groupedProjects.other.map((project) => (
              <SelectItem key={project.id} value={project.id}>
                <div className="flex items-center gap-2">
                  <FolderGit2 className="h-4 w-4" />
                  <span>{project.name}</span>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}
      </SelectContent>
    </Select>
  );
}
