"use client";

import { useMemo, useState, useEffect } from "react";
import { useProjects } from "@/hooks/use-projects";
import { useUIStore } from "@/store/ui-store";
import { Team } from "@/types";
import type { ProjectSummary } from "@/types";
import { OfflineState } from "@/components/ui/offline-state";
import { CreateProjectDialog } from "@/components/projects/create-project-dialog";
import { ProjectFilters } from "@/components/projects/project-filters";
import { ProjectCardGrid } from "@/components/projects/project-card-grid";
import { ProjectTable, teamLabels } from "@/components/projects/project-table";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { HelpTip } from "@/components/ui/help-tip";
import { ArrowDown, ArrowUp, LayoutGrid, Table2 } from "lucide-react";
import { usePageRefresh } from "@/hooks";

type ProjectSortKey = "name" | "cell";
type SortDirection = "asc" | "desc";

const SORT_OPTIONS: { value: ProjectSortKey; label: string }[] = [
  { value: "name", label: "Name" },
  { value: "cell", label: "Cell" },
];

function sortProjects(
  projects: ProjectSummary[],
  key: ProjectSortKey,
  direction: SortDirection,
): ProjectSummary[] {
  const sorted = [...projects].sort((a, b) =>
    key === "name"
      ? a.name.localeCompare(b.name)
      : teamLabels[a.assigned_cell].localeCompare(teamLabels[b.assigned_cell]),
  );
  return direction === "asc" ? sorted : sorted.reverse();
}

/** Projects tab content — extracted from the standalone /projects page so it
 * can live inside the Workstation tab shell (see workstation/page.tsx).
 *
 * Filter state is LOCAL, deliberately not URL params: every URL write forks
 * ScrollRestoration's route key and force-scrolls <main> to top (the same
 * bug class fixed in the work-sessions view — {scroll:false} does not
 * prevent it). `tab` stays URL-owned by the workstation page shell; these
 * filters don't ride the URL at all, so they reset on tab/page leave. */
export function ProjectsView() {
  const [searchQuery, setSearchQuery] = useState("");
  const [cellFilter, setCellFilter] = useState<Team[]>([]);
  const [showInactive, setShowInactive] = useState(false);
  const view = useUIStore((s) => s.projectsView);
  const setView = useUIStore((s) => s.setProjectsView);
  const [sortKey, setSortKey] = useState<ProjectSortKey>("name");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

  // Fetch projects
  const {
    data: projects,
    isLoading,
    error,
    refetch,
  } = useProjects({
    active_only: !showInactive,
  });

  const { register, unregister, refresh } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  // Filter projects client-side for search and multi-select cell filter
  const filteredProjects = useMemo(() => {
    if (!projects) return [];

    return projects.filter((project) => {
      // Search filter
      if (
        searchQuery &&
        !project.name.toLowerCase().includes(searchQuery.toLowerCase())
      ) {
        return false;
      }

      // Cell filter (if any selected, project must match one of them)
      if (
        cellFilter.length > 0 &&
        !cellFilter.includes(project.assigned_cell)
      ) {
        return false;
      }

      return true;
    });
  }, [projects, searchQuery, cellFilter]);

  // Sorting is client-side over the filtered list — only relevant to the
  // card view; the table keeps its own (unsorted, filter-order) render.
  const sortedProjects = useMemo(
    () => sortProjects(filteredProjects, sortKey, sortDirection),
    [filteredProjects, sortKey, sortDirection],
  );

  // Check if it's a connection error (backend not running)
  const isOffline =
    error &&
    (error.message?.includes("Network Error") ||
      error.message?.includes("ECONNREFUSED") ||
      (error as { code?: string })?.code === "ERR_NETWORK");

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Projects</h1>
          <p className="text-muted-foreground">
            Manage git repositories and track development work
          </p>
        </div>
        <div className="flex items-center gap-2">
          {view === "cards" && (
            <>
              <Select
                value={sortKey}
                onValueChange={(v) => setSortKey(v as ProjectSortKey)}
              >
                <HelpTip label="Sort the project cards by this field">
                  <SelectTrigger size="sm" className="w-auto min-w-32">
                    <SelectValue />
                  </SelectTrigger>
                </HelpTip>
                <SelectContent>
                  {SORT_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <HelpTip
                label={
                  sortDirection === "asc"
                    ? "Ascending — click for descending"
                    : "Descending — click for ascending"
                }
              >
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() =>
                    setSortDirection((d) => (d === "asc" ? "desc" : "asc"))
                  }
                  aria-label="Toggle sort direction"
                >
                  {sortDirection === "asc" ? (
                    <ArrowUp className="h-4 w-4" />
                  ) : (
                    <ArrowDown className="h-4 w-4" />
                  )}
                </Button>
              </HelpTip>
            </>
          )}
          <div className="flex items-center gap-1 rounded-md border p-0.5">
            <HelpTip label="Card view — boxes with badges, one per project">
              <Button
                type="button"
                variant={view === "cards" ? "secondary" : "ghost"}
                size="sm"
                className="h-7 px-2"
                aria-pressed={view === "cards"}
                aria-label="Card view"
                onClick={() => setView("cards")}
              >
                <LayoutGrid className="h-3.5 w-3.5" />
              </Button>
            </HelpTip>
            <HelpTip label="Table view">
              <Button
                type="button"
                variant={view === "table" ? "secondary" : "ghost"}
                size="sm"
                className="h-7 px-2"
                aria-pressed={view === "table"}
                aria-label="Table view"
                onClick={() => setView("table")}
              >
                <Table2 className="h-3.5 w-3.5" />
              </Button>
            </HelpTip>
          </div>
          <CreateProjectDialog />
        </div>
      </div>

      {/* Filters - Sticky */}
      <div className="sticky top-0 z-10 -mx-6 px-6 py-2 bg-muted/30 backdrop-blur-sm">
        <ProjectFilters
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          cellFilter={cellFilter}
          onCellChange={setCellFilter}
          showInactive={showInactive}
          onShowInactiveChange={setShowInactive}
        />
      </div>

      {/* Content */}
      {isOffline ? (
        <OfflineState
          title="Cannot Load Projects"
          description="Start the RoboCo orchestrator to manage projects. Projects track git repositories for agent work."
          onRetry={() => void refresh()}
        />
      ) : view === "cards" ? (
        <ProjectCardGrid projects={sortedProjects} isLoading={isLoading} />
      ) : (
        <ProjectTable projects={filteredProjects} isLoading={isLoading} />
      )}
    </div>
  );
}
