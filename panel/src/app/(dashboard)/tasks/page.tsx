"use client";

import { Suspense, useMemo, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useTasks } from "@/hooks/use-tasks";
import { TaskStatus, Team, TaskType } from "@/types";
import { OfflineState } from "@/components/ui/offline-state";
import { CreateTaskDialog, TaskFilters, TaskTable, SortField, SortDirection } from "@/components/tasks";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RefreshCw } from "lucide-react";

function TasksPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read state from URL params
  const searchQuery = searchParams.get("q") || "";
  const statusParam = searchParams.get("status");
  const statusFilter = useMemo(
    () => (statusParam?.split(",").filter(Boolean) as TaskStatus[]) || [],
    [statusParam]
  );
  const teamParam = searchParams.get("team");
  const teamFilter = useMemo(
    () => (teamParam?.split(",").filter(Boolean) as Team[]) || [],
    [teamParam]
  );
  const taskTypeParam = searchParams.get("type");
  const taskTypeFilter = useMemo(
    () => (taskTypeParam?.split(",").filter(Boolean) as TaskType[]) || [],
    [taskTypeParam]
  );

  // Table state from URL
  const sortField = (searchParams.get("sortBy") as SortField) || "created_at";
  const sortDir = (searchParams.get("sortDir") as SortDirection) || "desc";
  const currentPage = parseInt(searchParams.get("page") || "1", 10);
  const pageSize = parseInt(searchParams.get("size") || "25", 10);
  const expandedParam = searchParams.get("expanded");
  const expandedIds = useMemo(
    () => new Set(expandedParam?.split(",").filter(Boolean) || []),
    [expandedParam]
  );

  // Update URL params
  const updateParams = useCallback((updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString());
    Object.entries(updates).forEach(([key, value]) => {
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
    });
    const query = params.toString();
    router.push(query ? `/tasks?${query}` : "/tasks");
  }, [router, searchParams]);

  const handleSearchChange = useCallback((value: string) => {
    updateParams({ q: value || null });
  }, [updateParams]);

  const handleStatusChange = useCallback((value: TaskStatus[]) => {
    updateParams({ status: value.length > 0 ? value.join(",") : null });
  }, [updateParams]);

  const handleTeamChange = useCallback((value: Team[]) => {
    updateParams({ team: value.length > 0 ? value.join(",") : null });
  }, [updateParams]);

  const handleTaskTypeChange = useCallback((value: TaskType[]) => {
    updateParams({ type: value.length > 0 ? value.join(",") : null });
  }, [updateParams]);

  // Table state handlers
  const handleSortChange = useCallback((field: SortField, direction: SortDirection | null) => {
    if (direction === null) {
      updateParams({ sortBy: null, sortDir: null, page: null });
    } else {
      updateParams({
        sortBy: field === "created_at" ? null : field,
        sortDir: direction === "desc" ? null : direction,
        page: null,
      });
    }
  }, [updateParams]);

  const handlePageChange = useCallback((page: number) => {
    updateParams({ page: page === 1 ? null : String(page) });
  }, [updateParams]);

  const handlePageSizeChange = useCallback((size: number) => {
    updateParams({ size: size === 25 ? null : String(size), page: null });
  }, [updateParams]);

  const handleExpandedChange = useCallback((ids: Set<string>) => {
    updateParams({ expanded: ids.size > 0 ? Array.from(ids).join(",") : null });
  }, [updateParams]);

  // Fetch all tasks and filter client-side for multi-select
  const { data: tasks, isLoading, error, refetch } = useTasks();

  // Filter tasks based on multi-select filters
  const filteredTasks = useMemo(() => {
    if (!tasks) return [];

    return tasks.filter((task) => {
      // Search filter
      if (searchQuery && !task.title.toLowerCase().includes(searchQuery.toLowerCase())) {
        return false;
      }

      // Status filter (if any selected, task must match one of them)
      if (statusFilter.length > 0 && !statusFilter.includes(task.status)) {
        return false;
      }

      // Team filter (if any selected, task must match one of them)
      if (teamFilter.length > 0 && !teamFilter.includes(task.team)) {
        return false;
      }

      // Task type filter (if any selected, task must match one of them)
      // Note: task_type may be undefined until backend adds it to TaskResponse
      if (taskTypeFilter.length > 0 && task.task_type && !taskTypeFilter.includes(task.task_type)) {
        return false;
      }

      return true;
    });
  }, [tasks, searchQuery, statusFilter, teamFilter, taskTypeFilter]);

  // Check if it's a connection error (backend not running)
  const isOffline = error && (
    error.message?.includes("Network Error") ||
    error.message?.includes("ECONNREFUSED") ||
    (error as { code?: string })?.code === "ERR_NETWORK"
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Tasks</h1>
          <p className="text-muted-foreground">
            Manage and track all tasks across teams
          </p>
        </div>
        <div className="flex items-center gap-2">
          <CreateTaskDialog />
          <Button variant="outline" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filters - Sticky */}
      <div className="sticky top-0 z-10 -mx-6 px-6 py-2 bg-muted/30 backdrop-blur-sm">
        <TaskFilters
          searchQuery={searchQuery}
          onSearchChange={handleSearchChange}
          statusFilter={statusFilter}
          onStatusChange={handleStatusChange}
          teamFilter={teamFilter}
          onTeamChange={handleTeamChange}
          taskTypeFilter={taskTypeFilter}
          onTaskTypeChange={handleTaskTypeChange}
        />
      </div>

      {/* Content */}
      {isOffline ? (
        <OfflineState
          title="Cannot Load Tasks"
          description="Start the RoboCo orchestrator to manage tasks. Tasks you create will be picked up by agents when the backend is running."
          onRetry={() => refetch()}
        />
      ) : (
        <TaskTable
          tasks={filteredTasks}
          isLoading={isLoading}
          sortField={sortField}
          sortDirection={sortDir}
          onSortChange={handleSortChange}
          currentPage={currentPage}
          pageSize={pageSize}
          onPageChange={handlePageChange}
          onPageSizeChange={handlePageSizeChange}
          expandedIds={expandedIds}
          onExpandedChange={handleExpandedChange}
        />
      )}
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function TasksPage() {
  return (
    <Suspense fallback={
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
    }>
      <TasksPageContent />
    </Suspense>
  );
}
