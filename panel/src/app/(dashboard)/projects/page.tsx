"use client";

import { Suspense, useMemo, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useProjects } from "@/hooks/use-projects";
import { Team } from "@/types";
import { OfflineState } from "@/components/ui/offline-state";
import { CreateProjectDialog, ProjectFilters, ProjectTable } from "@/components/projects";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RefreshCw } from "lucide-react";

function ProjectsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read state from URL params
  const searchQuery = searchParams.get("q") || "";
  const cellFilterParam = searchParams.get("cell");
  const cellFilter = useMemo(
    () => (cellFilterParam?.split(",").filter(Boolean) as Team[]) || [],
    [cellFilterParam]
  );
  const showInactive = searchParams.get("inactive") === "true";

  // Update URL params
  const updateParams = useCallback(
    (updates: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString());
      Object.entries(updates).forEach(([key, value]) => {
        if (value) {
          params.set(key, value);
        } else {
          params.delete(key);
        }
      });
      const query = params.toString();
      router.push(query ? `/projects?${query}` : "/projects");
    },
    [router, searchParams]
  );

  const handleSearchChange = useCallback(
    (value: string) => {
      updateParams({ q: value || null });
    },
    [updateParams]
  );

  const handleCellChange = useCallback(
    (value: Team[]) => {
      updateParams({ cell: value.length > 0 ? value.join(",") : null });
    },
    [updateParams]
  );

  const handleShowInactiveChange = useCallback(
    (value: boolean) => {
      updateParams({ inactive: value ? "true" : null });
    },
    [updateParams]
  );

  // Fetch projects
  const { data: projects, isLoading, error, refetch } = useProjects({
    active_only: !showInactive,
  });

  // Filter projects client-side for search and multi-select cell filter
  const filteredProjects = useMemo(() => {
    if (!projects) return [];

    return projects.filter((project) => {
      // Search filter
      if (searchQuery && !project.name.toLowerCase().includes(searchQuery.toLowerCase())) {
        return false;
      }

      // Cell filter (if any selected, project must match one of them)
      if (cellFilter.length > 0 && !cellFilter.includes(project.assigned_cell)) {
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
          <Button variant="outline" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filters - Sticky */}
      <div className="sticky top-0 z-10 -mx-6 px-6 py-2 bg-muted/30 backdrop-blur-sm">
        <ProjectFilters
          searchQuery={searchQuery}
          onSearchChange={handleSearchChange}
          cellFilter={cellFilter}
          onCellChange={handleCellChange}
          showInactive={showInactive}
          onShowInactiveChange={handleShowInactiveChange}
        />
      </div>

      {/* Content */}
      {isOffline ? (
        <OfflineState
          title="Cannot Load Projects"
          description="Start the RoboCo orchestrator to manage projects. Projects track git repositories for agent work."
          onRetry={() => refetch()}
        />
      ) : (
        <ProjectTable projects={filteredProjects} isLoading={isLoading} />
      )}
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function ProjectsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <Skeleton className="h-9 w-32 mb-2" />
              <Skeleton className="h-5 w-64" />
            </div>
          </div>
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <ProjectsPageContent />
    </Suspense>
  );
}
