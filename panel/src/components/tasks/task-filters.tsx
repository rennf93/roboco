"use client";

import { TaskStatus, Team, TaskType } from "@/types";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ChevronDown, X, AlertOctagon, AlertCircle } from "lucide-react";
import { HelpTip } from "@/components/ui/help-tip";

interface TaskFiltersProps {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  statusFilter: TaskStatus[];
  onStatusChange: (value: TaskStatus[]) => void;
  teamFilter: Team[];
  onTeamChange: (value: Team[]) => void;
  // Optional new filters
  taskTypeFilter?: TaskType[];
  onTaskTypeChange?: (value: TaskType[]) => void;
  // Optional project / product filters (dynamic options from the API)
  projectFilter?: string[];
  onProjectChange?: (value: string[]) => void;
  projectOptions?: { value: string; label: string }[];
  productFilter?: string[];
  onProductChange?: (value: string[]) => void;
  productOptions?: { value: string; label: string }[];
  // Stalled-only toggle, backed by GET /tasks/blocked filtered by
  // blocker_resolver_type=human (see useStalledTasks) —
  // stalledCount/stalledError describe the state of that fetch, not a
  // client-derived stall condition.
  stalledFilter?: boolean;
  onStalledChange?: (value: boolean) => void;
  stalledCount?: number;
  stalledError?: boolean;
}

const STATUS_LABELS: Record<TaskStatus, string> = {
  [TaskStatus.BACKLOG]: "Backlog",
  [TaskStatus.PENDING]: "Pending",
  [TaskStatus.CLAIMED]: "Claimed",
  [TaskStatus.IN_PROGRESS]: "In Progress",
  [TaskStatus.BLOCKED]: "Blocked",
  [TaskStatus.PAUSED]: "Paused",
  [TaskStatus.VERIFYING]: "Verifying",
  [TaskStatus.NEEDS_REVISION]: "Needs Revision",
  [TaskStatus.AWAITING_QA]: "Awaiting QA",
  [TaskStatus.AWAITING_DOCUMENTATION]: "Awaiting Docs",
  [TaskStatus.AWAITING_PR_REVIEW]: "Awaiting PR Review",
  [TaskStatus.AWAITING_PM_REVIEW]: "Awaiting PM Review",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "Awaiting CEO Approval",
  [TaskStatus.COMPLETED]: "Completed",
  [TaskStatus.CANCELLED]: "Cancelled",
};

const TEAM_LABELS: Record<Team, string> = {
  [Team.BOARD]: "Board",
  [Team.MAIN_PM]: "Main PM",
  [Team.BACKEND]: "Backend",
  [Team.FRONTEND]: "Frontend",
  [Team.UX_UI]: "UX/UI",
  [Team.MARKETING]: "Marketing",
};

const TASK_TYPE_LABELS: Record<TaskType, string> = {
  [TaskType.CODE]: "Code",
  [TaskType.DOCUMENTATION]: "Documentation",
  [TaskType.RESEARCH]: "Research",
  [TaskType.PLANNING]: "Planning",
  [TaskType.DESIGN]: "Design",
  [TaskType.ADMINISTRATIVE]: "Administrative",
};

export function TaskFilters({
  searchQuery,
  onSearchChange,
  statusFilter,
  onStatusChange,
  teamFilter,
  onTeamChange,
  taskTypeFilter = [],
  onTaskTypeChange,
  projectFilter = [],
  onProjectChange,
  projectOptions = [],
  productFilter = [],
  onProductChange,
  productOptions = [],
  stalledFilter = false,
  onStalledChange,
  stalledCount,
  stalledError = false,
}: TaskFiltersProps) {
  const toggleStatus = (status: TaskStatus) => {
    if (statusFilter.includes(status)) {
      onStatusChange(statusFilter.filter((s) => s !== status));
    } else {
      onStatusChange([...statusFilter, status]);
    }
  };

  const toggleTeam = (team: Team) => {
    if (teamFilter.includes(team)) {
      onTeamChange(teamFilter.filter((t) => t !== team));
    } else {
      onTeamChange([...teamFilter, team]);
    }
  };

  const toggleTaskType = (type: TaskType) => {
    if (!onTaskTypeChange) return;
    if (taskTypeFilter.includes(type)) {
      onTaskTypeChange(taskTypeFilter.filter((t) => t !== type));
    } else {
      onTaskTypeChange([...taskTypeFilter, type]);
    }
  };

  const toggleProject = (id: string) => {
    if (!onProjectChange) return;
    onProjectChange(
      projectFilter.includes(id)
        ? projectFilter.filter((p) => p !== id)
        : [...projectFilter, id],
    );
  };

  const toggleProduct = (id: string) => {
    if (!onProductChange) return;
    onProductChange(
      productFilter.includes(id)
        ? productFilter.filter((p) => p !== id)
        : [...productFilter, id],
    );
  };

  const clearStatuses = () => onStatusChange([]);
  const clearTeams = () => onTeamChange([]);
  const clearTaskTypes = () => onTaskTypeChange?.([]);
  const clearProjects = () => onProjectChange?.([]);
  const clearProducts = () => onProductChange?.([]);
  const projectLabel = (id: string) =>
    projectOptions.find((o) => o.value === id)?.label ?? id;
  const productLabel = (id: string) =>
    productOptions.find((o) => o.value === id)?.label ?? id;

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex flex-col sm:flex-row gap-4">
          <div className="flex-1">
            <HelpTip label="Debounced server-side search across title, description, and task ID prefix.">
              <Input
                placeholder="Search tasks..."
                value={searchQuery}
                onChange={(e) => onSearchChange(e.target.value)}
              />
            </HelpTip>
          </div>
          <div className="flex flex-wrap gap-2">
            {/* Status Multi-Select */}
            <Popover>
              <HelpTip label="Multi-select — pick any number of statuses; the list shows tasks matching any of them.">
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    className="min-w-32 justify-between"
                  >
                    <span className="truncate">
                      {statusFilter.length === 0
                        ? "All Statuses"
                        : statusFilter.length === 1
                          ? STATUS_LABELS[statusFilter[0]]
                          : `${statusFilter.length} statuses`}
                    </span>
                    <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                  </Button>
                </PopoverTrigger>
              </HelpTip>
              <PopoverContent className="w-56 p-2" align="start">
                <div className="flex items-center justify-between mb-2 pb-2 border-b">
                  <span className="text-sm font-medium">Status</span>
                  {statusFilter.length > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2 text-xs"
                      onClick={clearStatuses}
                    >
                      Clear
                    </Button>
                  )}
                </div>
                <div className="space-y-1">
                  {Object.values(TaskStatus).map((status) => (
                    <label
                      key={status}
                      className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-muted cursor-pointer"
                    >
                      <Checkbox
                        checked={statusFilter.includes(status)}
                        onCheckedChange={() => toggleStatus(status)}
                      />
                      <span className="text-sm">{STATUS_LABELS[status]}</span>
                    </label>
                  ))}
                </div>
              </PopoverContent>
            </Popover>

            {/* Team Multi-Select */}
            <Popover>
              <HelpTip label="Multi-select — pick any number of teams; the list shows tasks matching any of them.">
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    className="min-w-32 justify-between"
                  >
                    <span className="truncate">
                      {teamFilter.length === 0
                        ? "All Teams"
                        : teamFilter.length === 1
                          ? TEAM_LABELS[teamFilter[0]]
                          : `${teamFilter.length} teams`}
                    </span>
                    <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                  </Button>
                </PopoverTrigger>
              </HelpTip>
              <PopoverContent className="w-48 p-2" align="start">
                <div className="flex items-center justify-between mb-2 pb-2 border-b">
                  <span className="text-sm font-medium">Team</span>
                  {teamFilter.length > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2 text-xs"
                      onClick={clearTeams}
                    >
                      Clear
                    </Button>
                  )}
                </div>
                <div className="space-y-1">
                  {Object.values(Team).map((team) => (
                    <label
                      key={team}
                      className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-muted cursor-pointer"
                    >
                      <Checkbox
                        checked={teamFilter.includes(team)}
                        onCheckedChange={() => toggleTeam(team)}
                      />
                      <span className="text-sm">{TEAM_LABELS[team]}</span>
                    </label>
                  ))}
                </div>
              </PopoverContent>
            </Popover>

            {/* Task Type Multi-Select (optional) */}
            {onTaskTypeChange && (
              <Popover>
                <HelpTip label="Multi-select — pick any number of task types; the list shows tasks matching any of them.">
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      className="min-w-32 justify-between"
                    >
                      <span className="truncate">
                        {taskTypeFilter.length === 0
                          ? "All Types"
                          : taskTypeFilter.length === 1
                            ? TASK_TYPE_LABELS[taskTypeFilter[0]]
                            : `${taskTypeFilter.length} types`}
                      </span>
                      <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                </HelpTip>
                <PopoverContent className="w-48 p-2" align="start">
                  <div className="flex items-center justify-between mb-2 pb-2 border-b">
                    <span className="text-sm font-medium">Task Type</span>
                    {taskTypeFilter.length > 0 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 text-xs"
                        onClick={clearTaskTypes}
                      >
                        Clear
                      </Button>
                    )}
                  </div>
                  <div className="space-y-1">
                    {Object.values(TaskType).map((type) => (
                      <label
                        key={type}
                        className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-muted cursor-pointer"
                      >
                        <Checkbox
                          checked={taskTypeFilter.includes(type)}
                          onCheckedChange={() => toggleTaskType(type)}
                        />
                        <span className="text-sm">
                          {TASK_TYPE_LABELS[type]}
                        </span>
                      </label>
                    ))}
                  </div>
                </PopoverContent>
              </Popover>
            )}

            {/* Project Multi-Select (optional) */}
            {onProjectChange && projectOptions.length > 0 && (
              <Popover>
                <HelpTip label="Multi-select — pick any number of projects; the list shows tasks matching any of them.">
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      className="min-w-32 justify-between"
                    >
                      <span className="truncate">
                        {projectFilter.length === 0
                          ? "All Projects"
                          : projectFilter.length === 1
                            ? projectLabel(projectFilter[0])
                            : `${projectFilter.length} projects`}
                      </span>
                      <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                </HelpTip>
                <PopoverContent className="w-56 p-2" align="start">
                  <div className="flex items-center justify-between mb-2 pb-2 border-b">
                    <span className="text-sm font-medium">Project</span>
                    {projectFilter.length > 0 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 text-xs"
                        onClick={clearProjects}
                      >
                        Clear
                      </Button>
                    )}
                  </div>
                  <div className="space-y-1 max-h-64 overflow-y-auto">
                    {projectOptions.map((opt) => (
                      <label
                        key={opt.value}
                        className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-muted cursor-pointer"
                      >
                        <Checkbox
                          checked={projectFilter.includes(opt.value)}
                          onCheckedChange={() => toggleProject(opt.value)}
                        />
                        <span className="text-sm">{opt.label}</span>
                      </label>
                    ))}
                  </div>
                </PopoverContent>
              </Popover>
            )}

            {/* Product Multi-Select (optional) */}
            {onProductChange && productOptions.length > 0 && (
              <Popover>
                <HelpTip label="Multi-select — pick any number of products; the list shows tasks matching any of them.">
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      className="min-w-32 justify-between"
                    >
                      <span className="truncate">
                        {productFilter.length === 0
                          ? "All Products"
                          : productFilter.length === 1
                            ? productLabel(productFilter[0])
                            : `${productFilter.length} products`}
                      </span>
                      <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                </HelpTip>
                <PopoverContent className="w-56 p-2" align="start">
                  <div className="flex items-center justify-between mb-2 pb-2 border-b">
                    <span className="text-sm font-medium">Product</span>
                    {productFilter.length > 0 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 text-xs"
                        onClick={clearProducts}
                      >
                        Clear
                      </Button>
                    )}
                  </div>
                  <div className="space-y-1 max-h-64 overflow-y-auto">
                    {productOptions.map((opt) => (
                      <label
                        key={opt.value}
                        className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-muted cursor-pointer"
                      >
                        <Checkbox
                          checked={productFilter.includes(opt.value)}
                          onCheckedChange={() => toggleProduct(opt.value)}
                        />
                        <span className="text-sm">{opt.label}</span>
                      </label>
                    ))}
                  </div>
                </PopoverContent>
              </Popover>
            )}

            {/* Stalled-only toggle (optional) */}
            {onStalledChange && (
              <HelpTip
                label={
                  stalledError
                    ? "Couldn't load the stalled-task set — try again shortly"
                    : "Tasks the dispatcher's respawn breaker has given up on"
                }
              >
                <Button
                  type="button"
                  variant={stalledFilter ? "default" : "outline"}
                  className="min-w-32 justify-between"
                  aria-pressed={stalledFilter}
                  onClick={() => onStalledChange(!stalledFilter)}
                >
                  <span className="flex items-center gap-1.5 truncate">
                    <AlertOctagon className="h-4 w-4 shrink-0" />
                    Stalled
                    {stalledError ? (
                      <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
                    ) : (
                      typeof stalledCount === "number" && (
                        <span className="text-xs">({stalledCount})</span>
                      )
                    )}
                  </span>
                </Button>
              </HelpTip>
            )}
          </div>
        </div>

        {/* Active Filters */}
        {(statusFilter.length > 0 ||
          teamFilter.length > 0 ||
          taskTypeFilter.length > 0 ||
          projectFilter.length > 0 ||
          productFilter.length > 0 ||
          stalledFilter) && (
          <div className="flex flex-wrap gap-2 mt-3 pt-3 border-t">
            {statusFilter.map((status) => (
              <Badge key={status} variant="secondary" className="gap-1">
                {STATUS_LABELS[status]}
                <HelpTip label="Remove this filter">
                  <X
                    className="h-3 w-3 cursor-pointer hover:text-destructive"
                    onClick={() => toggleStatus(status)}
                    aria-label={`Remove ${STATUS_LABELS[status]} filter`}
                  />
                </HelpTip>
              </Badge>
            ))}
            {teamFilter.map((team) => (
              <Badge key={team} variant="secondary" className="gap-1">
                {TEAM_LABELS[team]}
                <HelpTip label="Remove this filter">
                  <X
                    className="h-3 w-3 cursor-pointer hover:text-destructive"
                    onClick={() => toggleTeam(team)}
                    aria-label={`Remove ${TEAM_LABELS[team]} filter`}
                  />
                </HelpTip>
              </Badge>
            ))}
            {taskTypeFilter.map((type) => (
              <Badge key={type} variant="secondary" className="gap-1">
                {TASK_TYPE_LABELS[type]}
                <HelpTip label="Remove this filter">
                  <X
                    className="h-3 w-3 cursor-pointer hover:text-destructive"
                    onClick={() => toggleTaskType(type)}
                    aria-label={`Remove ${TASK_TYPE_LABELS[type]} filter`}
                  />
                </HelpTip>
              </Badge>
            ))}
            {projectFilter.map((id) => (
              <Badge key={id} variant="secondary" className="gap-1">
                {projectLabel(id)}
                <HelpTip label="Remove this filter">
                  <X
                    className="h-3 w-3 cursor-pointer hover:text-destructive"
                    onClick={() => toggleProject(id)}
                    aria-label={`Remove ${projectLabel(id)} filter`}
                  />
                </HelpTip>
              </Badge>
            ))}
            {productFilter.map((id) => (
              <Badge key={id} variant="secondary" className="gap-1">
                {productLabel(id)}
                <HelpTip label="Remove this filter">
                  <X
                    className="h-3 w-3 cursor-pointer hover:text-destructive"
                    onClick={() => toggleProduct(id)}
                    aria-label={`Remove ${productLabel(id)} filter`}
                  />
                </HelpTip>
              </Badge>
            ))}
            {stalledFilter && onStalledChange && (
              <Badge variant="secondary" className="gap-1">
                Stalled
                <HelpTip label="Remove this filter">
                  <X
                    className="h-3 w-3 cursor-pointer hover:text-destructive"
                    onClick={() => onStalledChange(false)}
                    aria-label="Remove Stalled filter"
                  />
                </HelpTip>
              </Badge>
            )}
            {(statusFilter.length > 0 ||
              teamFilter.length > 0 ||
              taskTypeFilter.length > 0 ||
              projectFilter.length > 0 ||
              productFilter.length > 0 ||
              stalledFilter) && (
              <HelpTip label="Removes every active filter above, restoring the unfiltered task list.">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs"
                  onClick={() => {
                    clearStatuses();
                    clearTeams();
                    clearTaskTypes();
                    clearProjects();
                    clearProducts();
                    onStalledChange?.(false);
                  }}
                >
                  Clear all
                </Button>
              </HelpTip>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
