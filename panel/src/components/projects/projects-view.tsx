"use client";

import { useMemo, useState, useEffect } from "react";
import { useProjects } from "@/hooks/use-projects";
import { Team } from "@/types";
import { OfflineState } from "@/components/ui/offline-state";
import { CreateProjectDialog } from "@/components/projects/create-project-dialog";
import { ProjectFilters } from "@/components/projects/project-filters";
import { ProjectTable } from "@/components/projects/project-table";
import { usePageRefresh } from "@/hooks";

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

  // Check if it's a connection error (backend not running)
  const isOffline =
    error &&
    (error.message?.includes("Network Error") ||
      error.message?.includes("ECONNREFUSED") ||
      (error as { code?: string })?.code === "ERR_NETWORK");

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Projects</h1>
          <p className="text-muted-foreground">
            Manage git repositories and track development work
          </p>
        </div>
        <div className="flex items-center gap-2">
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
      ) : (
        <ProjectTable projects={filteredProjects} isLoading={isLoading} />
      )}
    </div>
  );
}
