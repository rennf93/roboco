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
import {
  DevKanban,
  QaKanban,
  PrReviewKanban,
  PmKanban,
} from "@/components/kanban";
import type { TaskFilters as TaskApiFilters } from "@/lib/api/tasks";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { usePageRefresh } from "@/hooks";
import { useScrollRestorationStore } from "@/lib/stores";
import { HelpTip } from "@/components/ui/help-tip";
import { pickTab } from "@/lib/tabs";
import {
  List as ListIcon,
  LayoutGrid,
  Code,
  TestTube,
  GitPullRequest,
  ClipboardList,
} from "lucide-react";

type TasksViewTab = "list" | "kanban";
const TASKS_VIEW_TABS = ["list", "kanban"] as const satisfies readonly TasksViewTab[];
type KanbanView = "dev" | "qa" | "pr-review" | "pm";
const KANBAN_VIEWS = [
  "dev",
  "qa",
  "pr-review",
  "pm",
] as const satisfies readonly KanbanView[];

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

  // Top-level List|Kanban tab + Kanban sub-view, both URL-driven so they
  // share the same query-param state (including the filters above) across
  // tab switches.
  const activeTab = pickTab(searchParams.get("tab"), TASKS_VIEW_TABS, "list");
  const kanbanView = pickTab(searchParams.get("view"), KANBAN_VIEWS, "dev");

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
      // scroll: false — a UI-only param write (e.g. row expand/collapse)
      // must not reset scroll on its own; ScrollRestoration's route key
      // already excludes `expanded`, this is defense in depth.
      router.push(query ? `/tasks?${query}` : "/tasks", { scroll: false });
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

  const handleTabChange = useCallback(
    (value: string) => {
      updateParams({ tab: value === "list" ? null : value });
    },
    [updateParams],
  );

  const handleKanbanViewChange = useCallback(
    (value: string) => {
      updateParams({ view: value === "dev" ? null : value });
    },
    [updateParams],
  );

  // The Kanban views only support a single team selection, while the List
  // tab's team filter is multi-select — shared only when exactly one team is
  // active, otherwise the Kanban dropdown reads as "All Teams". Changing it
  // from either tab writes the same `team` URL param.
  const sharedKanbanTeam = teamFilter.length === 1 ? teamFilter[0] : undefined;
  const handleKanbanTeamChange = useCallback(
    (value: Team | undefined) => {
      handleTeamChange(value ? [value] : []);
    },
    [handleTeamChange],
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

  // Captures the table's live filtered/sorted order for task-detail prev/next
  // navigation (see useScrollRestorationStore.taskListNav).
  const setTaskListNav = useScrollRestorationStore(
    (state) => state.setTaskListNav,
  );
  const searchParamsString = searchParams.toString();
  const handleVisibleOrderChange = useCallback(
    (items: { id: string; title: string }[]) => {
      setTaskListNav({ items, queryString: searchParamsString });
    },
    [setTaskListNav, searchParamsString],
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
  const { data: projects, refetch: refetchProjects } = useProjects();
  const { data: products, refetch: refetchProducts } = useProducts();

  const { register, unregister, refresh } = usePageRefresh();

  useEffect(() => {
    const callbacks = [
      () => {
        void refetch();
      },
      () => {
        void refetchProjects();
      },
      () => {
        void refetchProducts();
      },
    ];
    callbacks.forEach((cb) => register(cb));
    return () => {
      callbacks.forEach((cb) => unregister(cb));
    };
  }, [register, unregister, refetch, refetchProjects, refetchProducts]);

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
          <HelpTip label="Filters, sort, and pagination all persist in the URL — this view is bookmarkable and shareable.">
            <p className="text-muted-foreground">
              Manage and track all tasks across teams
            </p>
          </HelpTip>
        </div>
        <div className="flex items-center gap-2">
          <CreateTaskDialog />
        </div>
      </div>

      {/* List | Kanban — top-level tabs, URL-driven so both share the same
          filter/search query-param state and switching tabs preserves it. */}
      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="list" className="gap-2">
            <ListIcon className="h-4 w-4" />
            List
          </TabsTrigger>
          <TabsTrigger value="kanban" className="gap-2">
            <LayoutGrid className="h-4 w-4" />
            Kanban
          </TabsTrigger>
        </TabsList>

        <TabsContent value="list" className="space-y-6 mt-6">
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

          {isOffline ? (
            <OfflineState
              title="Cannot Load Tasks"
              description="Start the RoboCo orchestrator to manage tasks. Tasks you create will be picked up by agents when the backend is running."
              onRetry={() => void refresh()}
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
              onVisibleOrderChange={handleVisibleOrderChange}
            />
          )}
        </TabsContent>

        <TabsContent value="kanban" className="mt-6">
          <Tabs value={kanbanView} onValueChange={handleKanbanViewChange}>
            <TabsList>
              {/* TooltipTrigger's asChild Slot merge clobbers TabsTrigger's
                  own data-state with the tooltip's — re-assert the real
                  selection state explicitly so data-[state=active] styling
                  survives (same fix as the standalone /kanban page). */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <TabsTrigger
                    value="dev"
                    data-state={kanbanView === "dev" ? "active" : "inactive"}
                    className="gap-2"
                  >
                    <Code className="h-4 w-4" />
                    Developer
                  </TabsTrigger>
                </TooltipTrigger>
                <TooltipContent>
                  Tasks claimed and worked by developers — backlog through
                  completion
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <TabsTrigger
                    value="qa"
                    data-state={kanbanView === "qa" ? "active" : "inactive"}
                    className="gap-2"
                  >
                    <TestTube className="h-4 w-4" />
                    QA
                  </TabsTrigger>
                </TooltipTrigger>
                <TooltipContent>
                  Quality assurance review workflow
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <TabsTrigger
                    value="pr-review"
                    data-state={
                      kanbanView === "pr-review" ? "active" : "inactive"
                    }
                    className="gap-2"
                  >
                    <GitPullRequest className="h-4 w-4" />
                    PR Review
                  </TabsTrigger>
                </TooltipTrigger>
                <TooltipContent>
                  In-path PR-review gate for assembled PRs, before the PM
                  merges
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <TabsTrigger
                    value="pm"
                    data-state={kanbanView === "pm" ? "active" : "inactive"}
                    className="gap-2"
                  >
                    <ClipboardList className="h-4 w-4" />
                    PM
                  </TabsTrigger>
                </TooltipTrigger>
                <TooltipContent>
                  Project management overview — every lifecycle state,
                  including recovery states
                </TooltipContent>
              </Tooltip>
            </TabsList>

            <TabsContent value="dev" className="mt-6">
              <DevKanban
                team={sharedKanbanTeam}
                onTeamChange={handleKanbanTeamChange}
              />
            </TabsContent>
            <TabsContent value="qa" className="mt-6">
              <QaKanban
                team={sharedKanbanTeam}
                onTeamChange={handleKanbanTeamChange}
              />
            </TabsContent>
            <TabsContent value="pr-review" className="mt-6">
              <PrReviewKanban
                team={sharedKanbanTeam}
                onTeamChange={handleKanbanTeamChange}
              />
            </TabsContent>
            <TabsContent value="pm" className="mt-6">
              <PmKanban
                team={sharedKanbanTeam}
                onTeamChange={handleKanbanTeamChange}
              />
            </TabsContent>
          </Tabs>
        </TabsContent>
      </Tabs>
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
