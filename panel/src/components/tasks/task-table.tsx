"use client";

import { useState, useMemo, useCallback, useEffect, memo } from "react";
import { Task } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ResponsiveTable,
  ResponsiveTableCardList,
  ResponsiveTableCard,
  ResponsiveTableCardRow,
  ResponsiveTableCardEmpty,
} from "@/components/ui/responsive-table";
import { TaskStatusBadge } from "./task-status-badge";
import { HelpTip } from "@/components/ui/help-tip";
import { TaskActions } from "./task-actions";
import { GitStatusBadge } from "./git-status-badge";
import Link from "next/link";
import {
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronRight as ChevronRightIcon,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { cn } from "@/lib/utils";

// Priority colors and labels
const priorityColors: Record<number, string> = {
  0: "bg-red-200 text-red-700",
  1: "bg-orange-200 text-orange-700",
  2: "bg-blue-200 text-blue-700",
  3: "bg-gray-200 text-gray-700",
};

const priorityLabels: Record<number, string> = {
  0: "P0 - Highest",
  1: "P1 - High",
  2: "P2 - Medium",
  3: "P3 - Low",
};

// Sorting types - exported for parent components
export type SortField =
  | "title"
  | "status"
  | "team"
  | "priority"
  | "assigned_to"
  | "created_at";
export type SortDirection = "asc" | "desc";

interface SortConfig {
  field: SortField;
  direction: SortDirection;
}

interface TaskTableProps {
  tasks: Task[] | undefined;
  isLoading: boolean;
  // id -> display name maps for the Project / Product column
  projectNames?: Record<string, string>;
  // id -> git_url, used to build clickable branch/PR links on the row badge
  projectGitUrls?: Record<string, string>;
  productNames?: Record<string, string>;
  // Controlled sort props (optional for backwards compatibility)
  sortField?: SortField;
  sortDirection?: SortDirection;
  onSortChange?: (field: SortField, direction: SortDirection | null) => void;
  // Controlled pagination props
  currentPage?: number;
  pageSize?: number;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  // Controlled expanded state
  expandedIds?: Set<string>;
  onExpandedChange?: (ids: Set<string>) => void;
  // Reports the currently visible, filtered + sorted task order (id + title
  // pairs) — consumed by the Tasks list page to power task-detail prev/next
  // navigation. Fires whenever the computed order changes.
  onVisibleOrderChange?: (items: { id: string; title: string }[]) => void;
}

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

// Build a tree structure from flat tasks
interface TaskTreeNode {
  task: Task;
  children: TaskTreeNode[];
  depth: number;
}

function buildTaskTree(tasks: Task[]): {
  roots: TaskTreeNode[];
  childrenMap: Map<string, Task[]>;
} {
  const taskMap = new Map<string, Task>();
  const childrenMap = new Map<string, Task[]>();

  // Index all tasks
  tasks.forEach((task) => {
    taskMap.set(task.id, task);
    if (task.parent_task_id) {
      const siblings = childrenMap.get(task.parent_task_id) || [];
      siblings.push(task);
      childrenMap.set(task.parent_task_id, siblings);
    }
  });

  // Build tree nodes for root tasks (no parent or parent not in current list)
  const roots: TaskTreeNode[] = [];

  function buildNode(task: Task, depth: number): TaskTreeNode {
    const children = (childrenMap.get(task.id) || []).map((child) =>
      buildNode(child, depth + 1),
    );
    return { task, children, depth };
  }

  tasks.forEach((task) => {
    // Root if no parent_task_id or parent is not in the current filtered list
    if (!task.parent_task_id || !taskMap.has(task.parent_task_id)) {
      roots.push(buildNode(task, 0));
    }
  });

  return { roots, childrenMap };
}

// Flatten tree for rendering, respecting expanded state
function flattenTree(
  nodes: TaskTreeNode[],
  expandedIds: Set<string>,
  result: TaskTreeNode[] = [],
): TaskTreeNode[] {
  nodes.forEach((node) => {
    result.push(node);
    if (node.children.length > 0 && expandedIds.has(node.task.id)) {
      flattenTree(node.children, expandedIds, result);
    }
  });
  return result;
}

function TaskTableSkeleton() {
  return (
    <>
      {Array.from({ length: 5 }).map((_, i) => (
        <TableRow key={i}>
          <TableCell>
            <Skeleton className="h-4 w-3/4" />
          </TableCell>
          <TableCell className="whitespace-nowrap">
            <Skeleton className="h-6 w-16" />
          </TableCell>
          <TableCell className="whitespace-nowrap">
            <Skeleton className="h-6 w-12" />
          </TableCell>
          <TableCell className="whitespace-nowrap">
            <Skeleton className="h-4 w-14" />
          </TableCell>
          <TableCell className="whitespace-nowrap">
            <Skeleton className="h-4 w-24" />
          </TableCell>
          <TableCell className="whitespace-nowrap">
            <Skeleton className="h-4 w-8" />
          </TableCell>
          <TableCell className="whitespace-nowrap">
            <Skeleton className="h-4 w-20" />
          </TableCell>
          <TableCell className="whitespace-nowrap">
            <Skeleton className="h-4 w-16" />
          </TableCell>
          <TableCell>
            <Skeleton className="h-8 w-8" />
          </TableCell>
        </TableRow>
      ))}
    </>
  );
}

function TaskTableEmpty() {
  return (
    <TableRow>
      <TableCell colSpan={9} className="text-center py-8">
        <div className="text-muted-foreground">No tasks found</div>
      </TableCell>
    </TableRow>
  );
}

function TaskCardSkeletons() {
  return (
    <ResponsiveTableCardList className="p-3">
      {Array.from({ length: 5 }).map((_, i) => (
        <ResponsiveTableCard key={i} className="space-y-2">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </ResponsiveTableCard>
      ))}
    </ResponsiveTableCardList>
  );
}

interface SortableHeaderProps {
  label: string;
  field: SortField;
  sortConfig: SortConfig | null;
  onSort: (field: SortField) => void;
  className?: string;
}

function SortableHeader({
  label,
  field,
  sortConfig,
  onSort,
  className,
}: SortableHeaderProps) {
  const isActive = sortConfig?.field === field;
  const direction = isActive ? sortConfig.direction : null;

  return (
    <TableHead className={className}>
      <HelpTip label="Sorts ascending, then descending, then clears — click again to cycle.">
        <Button
          onClick={() => onSort(field)}
          variant="ghost"
          className="h-auto -ml-2 px-2 py-1 gap-1 font-normal"
        >
          {label}
          {!isActive && (
            <ArrowUpDown className="h-4 w-4 text-muted-foreground" />
          )}
          {direction === "asc" && <ArrowUp className="h-4 w-4" />}
          {direction === "desc" && <ArrowDown className="h-4 w-4" />}
        </Button>
      </HelpTip>
    </TableHead>
  );
}

interface TaskRowProps {
  node: TaskTreeNode;
  isExpanded: boolean;
  childCount: number;
  projectNames: Record<string, string>;
  projectGitUrls: Record<string, string>;
  productNames: Record<string, string>;
  onToggleExpand: (taskId: string) => void;
}

// Memoized: a page renders up to 100 of these (PAGE_SIZE_OPTIONS caps there)
// and re-renders on every sort/page/expand change or unrelated parent update
// — memoizing stops an unchanged row's markup from being torn down and
// rebuilt every time. `node` stays referentially stable across React Query
// refetches for unchanged tasks (structural sharing); projectNames/
// projectGitUrls/productNames/onToggleExpand are stabilized by the caller.
const TaskTableRow = memo(function TaskTableRow({
  node,
  isExpanded,
  childCount,
  projectNames,
  projectGitUrls,
  productNames,
  onToggleExpand,
}: TaskRowProps) {
  const task = node.task;
  const hasChildren = node.children.length > 0;

  const handleRowClick = (e: React.MouseEvent) => {
    // Don't toggle if clicking on interactive elements
    const target = e.target as HTMLElement;
    if (
      target.closest("a") ||
      target.closest("button") ||
      target.closest('[role="button"]') ||
      target.closest("[data-no-expand]")
    ) {
      return;
    }
    if (hasChildren) {
      onToggleExpand(task.id);
    }
  };

  return (
    <TableRow
      className={cn(
        "hover:bg-muted/50",
        node.depth > 0 && "bg-muted/20",
        hasChildren && "cursor-pointer",
      )}
      onClick={handleRowClick}
    >
      <TableCell className="max-w-[22rem]">
        <div
          className="flex items-center gap-1 min-w-0"
          style={{ paddingLeft: `${node.depth * 1.5}rem` }}
        >
          {hasChildren ? (
            <HelpTip
              label={
                isExpanded
                  ? "Hides this task's subtasks"
                  : "Shows this task's subtasks inline"
              }
            >
              <Button
                onClick={() => onToggleExpand(task.id)}
                variant="ghost"
                size="icon-sm"
                className="p-0.5 h-5 w-5 shrink-0"
                aria-label={isExpanded ? "Collapse" : "Expand"}
              >
                {isExpanded ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRightIcon className="h-4 w-4" />
                )}
              </Button>
            </HelpTip>
          ) : (
            <span className="w-5 shrink-0" />
          )}
          <Link
            prefetch={false}
            href={"/tasks/" + task.id}
            className="block hover:underline min-w-0"
          >
            <div className="font-medium flex items-center gap-2 min-w-0">
              <span className="truncate" title={task.title}>
                {task.title}
              </span>
              {task.batch_id && !task.parent_task_id && (
                <HelpTip label="A multi-task batch — this is the umbrella task for a set of related tasks">
                  <Badge
                    variant="outline"
                    className="text-xs shrink-0 border-primary/50 text-primary"
                  >
                    MegaTask
                  </Badge>
                </HelpTip>
              )}
              {childCount > 0 && (
                <HelpTip label="Direct subtasks under this task, regardless of their current status.">
                  <Badge variant="secondary" className="text-xs shrink-0">
                    {childCount} subtask
                    {childCount !== 1 ? "s" : ""}
                  </Badge>
                </HelpTip>
              )}
            </div>
          </Link>
        </div>
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <TaskStatusBadge status={task.status} />
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <GitStatusBadge
          task={task}
          repoUrl={
            task.project_id ? projectGitUrls[task.project_id] : undefined
          }
        />
      </TableCell>
      <TableCell className="capitalize whitespace-nowrap">
        {task.team.replace(/_/g, " ")}
      </TableCell>
      <TableCell className="whitespace-nowrap text-sm">
        {task.project_id && projectNames[task.project_id] ? (
          <span>{projectNames[task.project_id]}</span>
        ) : task.product_id && productNames[task.product_id] ? (
          <span className="text-muted-foreground">
            {productNames[task.product_id]}{" "}
            <span className="text-xs">(product)</span>
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <Badge
          className={
            (priorityColors[task.priority] ?? priorityColors[2]) + " text-xs"
          }
        >
          {priorityLabels[task.priority] ?? "P2 - Medium"}
        </Badge>
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <HelpTip
          label={
            task.assigned_to
              ? "The agent currently pinned to this task."
              : "No agent pinned — the orchestrator routes it by role and availability."
          }
        >
          <Badge variant="outline">
            {getAgentDisplayName(task.assigned_to)}
          </Badge>
        </HelpTip>
      </TableCell>
      <TableCell className="text-muted-foreground text-sm whitespace-nowrap">
        <HelpTip label={new Date(task.created_at).toLocaleString()}>
          <span>
            {formatDistanceToNow(new Date(task.created_at), {
              addSuffix: true,
            })}
          </span>
        </HelpTip>
      </TableCell>
      <TableCell>
        <TaskActions task={task} />
      </TableCell>
    </TableRow>
  );
});

// Mobile card counterpart of TaskTableRow — same memoization rationale.
const TaskTableCard = memo(function TaskTableCard({
  node,
  isExpanded,
  childCount,
  projectNames,
  projectGitUrls,
  productNames,
  onToggleExpand,
}: TaskRowProps) {
  const task = node.task;
  const hasChildren = node.children.length > 0;

  return (
    <ResponsiveTableCard style={{ marginLeft: `${node.depth * 1}rem` }}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {hasChildren && (
              <HelpTip
                label={
                  isExpanded
                    ? "Hides this task's subtasks"
                    : "Shows this task's subtasks inline"
                }
              >
                <Button
                  onClick={() => onToggleExpand(task.id)}
                  variant="ghost"
                  size="icon-sm"
                  className="h-5 w-5 shrink-0 p-0.5"
                  aria-label={isExpanded ? "Collapse" : "Expand"}
                >
                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4" />
                  ) : (
                    <ChevronRightIcon className="h-4 w-4" />
                  )}
                </Button>
              </HelpTip>
            )}
            <Link
              prefetch={false}
              href={"/tasks/" + task.id}
              className="min-w-0 truncate font-medium hover:underline"
              title={task.title}
            >
              {task.title}
            </Link>
          </div>
          {(task.batch_id && !task.parent_task_id) || childCount > 0 ? (
            <div className="mt-1 flex flex-wrap gap-1">
              {task.batch_id && !task.parent_task_id && (
                <HelpTip label="A multi-task batch — this is the umbrella task for a set of related tasks">
                  <Badge
                    variant="outline"
                    className="border-primary/50 text-xs text-primary"
                  >
                    MegaTask
                  </Badge>
                </HelpTip>
              )}
              {childCount > 0 && (
                <HelpTip label="Direct subtasks under this task, regardless of their current status.">
                  <Badge variant="secondary" className="text-xs">
                    {childCount} subtask
                    {childCount !== 1 ? "s" : ""}
                  </Badge>
                </HelpTip>
              )}
            </div>
          ) : null}
        </div>
        <TaskActions task={task} />
      </div>

      <div className="mt-3 divide-y">
        <ResponsiveTableCardRow label="Status">
          <TaskStatusBadge status={task.status} />
        </ResponsiveTableCardRow>
        <ResponsiveTableCardRow label="Git">
          <GitStatusBadge
            task={task}
            repoUrl={
              task.project_id ? projectGitUrls[task.project_id] : undefined
            }
          />
        </ResponsiveTableCardRow>
        <ResponsiveTableCardRow label="Team">
          <span className="capitalize">{task.team.replace(/_/g, " ")}</span>
        </ResponsiveTableCardRow>
        <ResponsiveTableCardRow label="Project">
          {task.project_id && projectNames[task.project_id]
            ? projectNames[task.project_id]
            : task.product_id && productNames[task.product_id]
              ? `${productNames[task.product_id]} (product)`
              : "—"}
        </ResponsiveTableCardRow>
        <ResponsiveTableCardRow label="Priority">
          <Badge
            className={
              (priorityColors[task.priority] ?? priorityColors[2]) +
              " text-xs"
            }
          >
            {priorityLabels[task.priority] ?? "P2 - Medium"}
          </Badge>
        </ResponsiveTableCardRow>
        <ResponsiveTableCardRow label="Assigned">
          <HelpTip
            label={
              task.assigned_to
                ? "The agent currently pinned to this task."
                : "No agent pinned — the orchestrator routes it by role and availability."
            }
          >
            <Badge variant="outline">
              {getAgentDisplayName(task.assigned_to)}
            </Badge>
          </HelpTip>
        </ResponsiveTableCardRow>
        <ResponsiveTableCardRow label="Created">
          <HelpTip label={new Date(task.created_at).toLocaleString()}>
            <span>
              {formatDistanceToNow(new Date(task.created_at), {
                addSuffix: true,
              })}
            </span>
          </HelpTip>
        </ResponsiveTableCardRow>
      </div>
    </ResponsiveTableCard>
  );
});

export function TaskTable({
  tasks,
  isLoading,
  projectNames = {},
  projectGitUrls = {},
  productNames = {},
  sortField: controlledSortField,
  sortDirection: controlledSortDirection,
  onSortChange,
  currentPage: controlledCurrentPage,
  pageSize: controlledPageSize,
  onPageChange,
  onPageSizeChange,
  expandedIds: controlledExpandedIds,
  onExpandedChange,
  onVisibleOrderChange,
}: TaskTableProps) {
  // Internal state (used when not controlled)
  const [internalSortConfig, setInternalSortConfig] =
    useState<SortConfig | null>({
      field: "created_at",
      direction: "desc",
    });
  const [internalCurrentPage, setInternalCurrentPage] = useState(1);
  const [internalPageSize, setInternalPageSize] = useState(25);
  const [internalExpandedIds, setInternalExpandedIds] = useState<Set<string>>(
    new Set(),
  );

  // Use controlled or internal state
  const isControlled = onSortChange !== undefined;
  const sortConfig: SortConfig | null = useMemo(() => {
    if (isControlled) {
      return controlledSortField
        ? {
            field: controlledSortField,
            direction: controlledSortDirection || "desc",
          }
        : null;
    }
    return internalSortConfig;
  }, [
    isControlled,
    controlledSortField,
    controlledSortDirection,
    internalSortConfig,
  ]);
  const currentPage = controlledCurrentPage ?? internalCurrentPage;
  const pageSize = controlledPageSize ?? internalPageSize;
  const expandedIds = controlledExpandedIds ?? internalExpandedIds;

  const handleSort = (field: SortField) => {
    if (isControlled) {
      // Calculate new state and notify parent
      if (sortConfig?.field === field) {
        if (sortConfig.direction === "asc") {
          onSortChange(field, "desc");
        } else {
          onSortChange(field, null); // Clear sort
        }
      } else {
        onSortChange(field, "asc");
      }
    } else {
      setInternalSortConfig((prev) => {
        if (prev?.field === field) {
          if (prev.direction === "asc") {
            return { field, direction: "desc" };
          } else {
            return null;
          }
        }
        return { field, direction: "asc" };
      });
      setInternalCurrentPage(1);
    }
  };

  // Build tree and get children map for counting
  const { roots, childrenMap } = useMemo(() => {
    if (!tasks) return { roots: [], childrenMap: new Map() };
    return buildTaskTree(tasks);
  }, [tasks]);

  // Sort root nodes
  const sortedRoots = useMemo(() => {
    if (!sortConfig) return roots;

    const sortNodes = (nodes: TaskTreeNode[]): TaskTreeNode[] => {
      const sorted = [...nodes].sort((a, b) => {
        const { field, direction } = sortConfig;
        const multiplier = direction === "asc" ? 1 : -1;

        switch (field) {
          case "title":
            return multiplier * a.task.title.localeCompare(b.task.title);
          case "status":
            return multiplier * a.task.status.localeCompare(b.task.status);
          case "team":
            return multiplier * a.task.team.localeCompare(b.task.team);
          case "priority":
            return multiplier * (a.task.priority - b.task.priority);
          case "assigned_to":
            const aAssigned = a.task.assigned_to || "";
            const bAssigned = b.task.assigned_to || "";
            return multiplier * aAssigned.localeCompare(bAssigned);
          case "created_at":
            return (
              multiplier *
              (new Date(a.task.created_at).getTime() -
                new Date(b.task.created_at).getTime())
            );
          default:
            return 0;
        }
      });

      // Also sort children recursively
      return sorted.map((node) => ({
        ...node,
        children: sortNodes(node.children),
      }));
    };

    return sortNodes(roots);
  }, [roots, sortConfig]);

  // Flatten tree based on expanded state
  const flattenedTasks = useMemo(() => {
    return flattenTree(sortedRoots, expandedIds);
  }, [sortedRoots, expandedIds]);

  // Report the visible order to the parent so task-detail can compute
  // prev/next within this exact filter/sort context.
  useEffect(() => {
    onVisibleOrderChange?.(
      flattenedTasks.map((node) => ({
        id: node.task.id,
        title: node.task.title,
      })),
    );
  }, [flattenedTasks, onVisibleOrderChange]);

  // Pagination on flattened list
  const totalItems = flattenedTasks.length;
  const totalPages = Math.ceil(totalItems / pageSize);
  const startIndex = (currentPage - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, totalItems);
  const paginatedTasks = flattenedTasks.slice(startIndex, endIndex);

  const handlePageSizeChange = (value: string) => {
    const newSize = Number(value);
    if (onPageSizeChange) {
      onPageSizeChange(newSize);
    } else {
      setInternalPageSize(newSize);
      setInternalCurrentPage(1);
    }
  };

  const goToPage = (page: number) => {
    const newPage = Math.max(1, Math.min(page, totalPages));
    if (onPageChange) {
      onPageChange(newPage);
    } else {
      setInternalCurrentPage(newPage);
    }
  };

  // Stable across renders that don't touch expand state, so TaskTableRow's
  // memoization actually holds on an unrelated re-render (e.g. a sibling
  // page-chrome state change) instead of every row rebuilding regardless.
  const toggleExpand = useCallback(
    (taskId: string) => {
      const next = new Set(expandedIds);
      if (next.has(taskId)) {
        next.delete(taskId);
      } else {
        next.add(taskId);
      }
      if (onExpandedChange) {
        onExpandedChange(next);
      } else {
        setInternalExpandedIds(next);
      }
    },
    [expandedIds, onExpandedChange],
  );

  const expandAll = () => {
    const allParentIds = new Set<string>();
    tasks?.forEach((task) => {
      if (childrenMap.has(task.id)) {
        allParentIds.add(task.id);
      }
    });
    if (onExpandedChange) {
      onExpandedChange(allParentIds);
    } else {
      setInternalExpandedIds(allParentIds);
    }
  };

  const collapseAll = () => {
    if (onExpandedChange) {
      onExpandedChange(new Set());
    } else {
      setInternalExpandedIds(new Set());
    }
  };

  // Count how many tasks have children
  const hasAnyChildren = childrenMap.size > 0;

  return (
    <Card>
      <CardContent className="p-0">
        {/* Tree controls */}
        {hasAnyChildren && !isLoading && (
          <div className="flex items-center gap-2 px-4 py-2 border-b bg-muted/30">
            <span className="text-sm text-muted-foreground">Tree view:</span>
            <HelpTip label="Opens every parent row so all subtasks show inline, ignoring current sort.">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={expandAll}
              >
                Expand all
              </Button>
            </HelpTip>
            <HelpTip label="Hides every subtask row, showing only root-level tasks.">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={collapseAll}
              >
                Collapse all
              </Button>
            </HelpTip>
          </div>
        )}

        <ResponsiveTable
          table={
            <Table>
              <TableHeader>
                <TableRow>
                  <SortableHeader
                    label="Title"
                    field="title"
                    sortConfig={sortConfig}
                    onSort={handleSort}
                  />
                  <SortableHeader
                    label="Status"
                    field="status"
                    sortConfig={sortConfig}
                    onSort={handleSort}
                    className="whitespace-nowrap"
                  />
                  <HelpTip label="Branch, PR, or docs/PR progress — whichever this task's git workflow has reached.">
                    <TableHead className="whitespace-nowrap">Git</TableHead>
                  </HelpTip>
                  <SortableHeader
                    label="Team"
                    field="team"
                    sortConfig={sortConfig}
                    onSort={handleSort}
                    className="whitespace-nowrap"
                  />
                  <HelpTip label="A direct Project shows its name; a fan-out task shows its Product instead.">
                    <TableHead className="whitespace-nowrap">
                      Project / Product
                    </TableHead>
                  </HelpTip>
                  <SortableHeader
                    label="Priority"
                    field="priority"
                    sortConfig={sortConfig}
                    onSort={handleSort}
                    className="whitespace-nowrap"
                  />
                  <SortableHeader
                    label="Assigned To"
                    field="assigned_to"
                    sortConfig={sortConfig}
                    onSort={handleSort}
                    className="whitespace-nowrap"
                  />
                  <SortableHeader
                    label="Created"
                    field="created_at"
                    sortConfig={sortConfig}
                    onSort={handleSort}
                    className="whitespace-nowrap"
                  />
                  <TableHead className="w-10"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <TaskTableSkeleton />
                ) : paginatedTasks.length === 0 ? (
                  <TaskTableEmpty />
                ) : (
                  paginatedTasks.map((node) => (
                    <TaskTableRow
                      key={node.task.id}
                      node={node}
                      isExpanded={expandedIds.has(node.task.id)}
                      childCount={childrenMap.get(node.task.id)?.length || 0}
                      projectNames={projectNames}
                      projectGitUrls={projectGitUrls}
                      productNames={productNames}
                      onToggleExpand={toggleExpand}
                    />
                  ))
                )}
              </TableBody>
            </Table>
          }
          cards={
            isLoading ? (
              <TaskCardSkeletons />
            ) : paginatedTasks.length === 0 ? (
              <ResponsiveTableCardEmpty className="m-3">
                No tasks found
              </ResponsiveTableCardEmpty>
            ) : (
              <ResponsiveTableCardList className="p-3">
                {paginatedTasks.map((node) => (
                  <TaskTableCard
                    key={node.task.id}
                    node={node}
                    isExpanded={expandedIds.has(node.task.id)}
                    childCount={childrenMap.get(node.task.id)?.length || 0}
                    projectNames={projectNames}
                    projectGitUrls={projectGitUrls}
                    productNames={productNames}
                    onToggleExpand={toggleExpand}
                  />
                ))}
              </ResponsiveTableCardList>
            )
          }
        />

        {/* Pagination Controls */}
        {!isLoading && tasks && tasks.length > 0 && (
          <div className="flex flex-wrap items-center justify-end gap-4 px-4 py-3 border-t">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <HelpTip label="How many rows show per page; resets to page 1 when changed.">
                <span>Rows:</span>
              </HelpTip>
              <Select
                value={String(pageSize)}
                onValueChange={handlePageSizeChange}
              >
                <SelectTrigger className="w-auto min-w-14 h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZE_OPTIONS.map((size) => (
                    <SelectItem key={size} value={String(size)}>
                      {size}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <HelpTip label="Range within the current filtered/sorted, flattened tree view — not the total unfiltered task count.">
              <span className="text-sm text-muted-foreground">
                {startIndex + 1}-{endIndex} of {totalItems}
              </span>
            </HelpTip>
            <div className="flex items-center gap-1">
              <HelpTip label="Previous page.">
                <span
                  className="inline-block"
                  tabIndex={currentPage === 1 ? 0 : undefined}
                >
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => goToPage(currentPage - 1)}
                    disabled={currentPage === 1}
                    aria-label="Previous page"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                </span>
              </HelpTip>
              <HelpTip label="Next page.">
                <span
                  className="inline-block"
                  tabIndex={currentPage === totalPages ? 0 : undefined}
                >
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => goToPage(currentPage + 1)}
                    disabled={currentPage === totalPages}
                    aria-label="Next page"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </span>
              </HelpTip>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
