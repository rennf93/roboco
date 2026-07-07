"use client";

import { Suspense, useEffect, useMemo, useCallback, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useTasks } from "@/hooks/use-tasks";
import { useProjects } from "@/hooks/use-projects";
import { useProducts } from "@/hooks/use-products";
import { TaskStatus, Team, TaskType } from "@/types";
import { OfflineState } from "@/components/ui/offline-state";
import {
  CreateTaskDialog,
  TaskFilters,
  TaskTable,
  SortField,
  SortDirection,
} from "@/components/tasks";
import type { TaskFilters as TaskApiFilters } from "@/lib/api/tasks";
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
    [statusParam],
  );
  const teamParam = searchParams.get("team");
  const teamFilter = useMemo(
    () => (teamParam?.split(",").filter(Boolean) as Team[]) || [],
    [teamParam],
  );
  const taskTypeParam = searchParams.get("type");
  const taskTypeFilter = useMemo(
    () => (taskTypeParam?.split(",").filter(Boolean) as TaskType[]) || [],
    [taskTypeParam],
  );
  const projectParam = searchParams.get("project");
  const projectFilter = useMemo(
    () => projectParam?.split(",").filter(Boolean) || [],
    [projectParam],
  );
  const productParam = searchParams.get("product");
  const productFilter = useMemo(
    () => productParam?.split(",").filter(Boolean) || [],
    [productParam],
  );

  // Table state from URL
  const sortField = (searchParams.get("sortBy") as SortField) || "created_at";
  const sortDir = (searchParams.get("sortDir") as SortDirection) || "desc";
  const currentPage = parseInt(searchParams.get("page") || "1", 10);
  const pageSize = parseInt(searchParams.get("size") || "25", 10);
  const expandedParam = searchParams.get("expanded");
  const expandedIds = useMemo(
    () => new Set(expandedParam?.split(",").filter(Boolean) || []),
    [expandedParam],
  );

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
      router.push(query ? `/tasks?${query}` : "/tasks");
    },
    [router, searchParams],
  );

  const handleSearchChange = useCallback(
    (value: string) => {
      updateParams({ q: value || null });
    },
    [updateParams],
  );

  const handleStatusChange = useCallback(
    (value: TaskStatus[]) => {
      updateParams({ status: value.length > 0 ? value.join(",") : null });
    },
    [updateParams],
  );

  const handleTeamChange = useCallback(
    (value: Team[]) => {
      updateParams({ team: value.length > 0 ? value.join(",") : null });
    },
    [updateParams],
  );

  const handleTaskTypeChange = useCallback(
    (value: TaskType[]) => {
      updateParams({ type: value.length > 0 ? value.join(",") : null });
    },
    [updateParams],
  );

  const handleProjectChange = useCallback(
    (value: string[]) => {
      updateParams({ project: value.length > 0 ? value.join(",") : null });
    },
    [updateParams],
  );

  const handleProductChange = useCallback(
    (value: string[]) => {
      updateParams({ product: value.length > 0 ? value.join(",") : null });
    },
    [updateParams],
  );

  // Table state handlers
  const handleSortChange = useCallback(
    (field: SortField, direction: SortDirection | null) => {
      if (direction === null) {
        updateParams({ sortBy: null, sortDir: null, page: null });
      } else {
        updateParams({
          sortBy: field === "created_at" ? null : field,
          sortDir: direction === "desc" ? null : direction,
          page: null,
        });
      }
    },
    [updateParams],
  );

  const handlePageChange = useCallback(
    (page: number) => {
      updateParams({ page: page === 1 ? null : String(page) });
    },
    [updateParams],
  );

  const handlePageSizeChange = useCallback(
    (size: number) => {
      updateParams({ size: size === 25 ? null : String(size), page: null });
    },
    [updateParams],
  );

  const handleExpandedChange = useCallback(
    (ids: Set<string>) => {
      updateParams({
        expanded: ids.size > 0 ? Array.from(ids).join(",") : null,
      });
    },
    [updateParams],
  );

  // Fetch tasks (server-filtered for single-select status/team) + client-side multi-select extras
  // Debounced server-side search: title + description + id prefix. The
  // old client-side title-only filter hid description/id matches the
  // server now returns, so it is gone.
  const [debouncedQuery, setDebouncedQuery] = useState(searchQuery);
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(searchQuery), 300);
    return () => clearTimeout(handle);
  }, [searchQuery]);
  // Server-side filter: /tasks/summary accepts one status + one team. Single
  // selection rides the server; multi-select filters the extras client-side.
  const filters: TaskApiFilters | undefined = {
    limit: 500,
    ...(debouncedQuery ? { q: debouncedQuery } : {}),
    ...(statusFilter.length === 1 ? { status: statusFilter[0] } : {}),
    ...(teamFilter.length === 1 ? { team: teamFilter[0] } : {}),
  };
  const { data: tasks, isLoading, error, refetch } = useTasks(filters);

  // Projects + products: power the Project/Product filter options + name display.
  const { data: projects } = useProjects();
  const { data: products } = useProducts();
  const projectNames = useMemo(
    () => Object.fromEntries((projects ?? []).map((p) => [p.id, p.name])),
    [projects],
  );
  const projectGitUrls = useMemo(
    () => Object.fromEntries((projects ?? []).map((p) => [p.id, p.git_url])),
    [projects],
  );
  const productNames = useMemo(
    () => Object.fromEntries((products ?? []).map((p) => [p.id, p.name])),
    [products],
  );
  const projectOptions = useMemo(
    () => (projects ?? []).map((p) => ({ value: p.id, label: p.name })),
    [projects],
  );
  const productOptions = useMemo(
    () => (products ?? []).map((p) => ({ value: p.id, label: p.name })),
    [products],
  );

  // Client-side filter for fields the backend doesn't accept (task_type,
  // project, product) plus multi-select extras (status/team when > 1 — the
  // single-selection case is already applied server-side).
  const filteredTasks = useMemo(() => {
    if (!tasks) return [];

    return tasks.filter((task) => {
      // Status: server pre-filtered the single-select case.
      if (statusFilter.length > 1 && !statusFilter.includes(task.status)) {
        return false;
      }

      // Team: server pre-filtered the single-select case.
      if (teamFilter.length > 1 && !teamFilter.includes(task.team)) {
        return false;
      }

      // Task type filter (if any selected, task must match one of them)
      // Note: task_type may be undefined until backend adds it to TaskResponse
      if (
        taskTypeFilter.length > 0 &&
        task.task_type &&
        !taskTypeFilter.includes(task.task_type)
      ) {
        return false;
      }

      // Project filter (a task with no project_id is excluded when filtering by project)
      if (
        projectFilter.length > 0 &&
        (!task.project_id || !projectFilter.includes(task.project_id))
      ) {
        return false;
      }

      // Product filter (a task with no product_id is excluded when filtering by product)
      if (
        productFilter.length > 0 &&
        (!task.product_id || !productFilter.includes(task.product_id))
      ) {
        return false;
      }

      return true;
    });
  }, [
    tasks,
    statusFilter,
    teamFilter,
    taskTypeFilter,
    projectFilter,
    productFilter,
  ]);

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
          projectFilter={projectFilter}
          onProjectChange={handleProjectChange}
          projectOptions={projectOptions}
          productFilter={productFilter}
          onProductChange={handleProductChange}
          productOptions={productOptions}
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
          projectNames={projectNames}
          projectGitUrls={projectGitUrls}
          productNames={productNames}
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
      <TasksPageContent />
    </Suspense>
  );
}
