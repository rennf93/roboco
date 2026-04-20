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
import { ChevronDown, X } from "lucide-react";

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
  [TaskStatus.AWAITING_PM_REVIEW]: "Awaiting PM Review",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "Awaiting CEO Approval",
  [TaskStatus.COMPLETED]: "Completed",
  [TaskStatus.CANCELLED]: "Cancelled",
  [TaskStatus.QUARANTINED]: "Quarantined",
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

  const clearStatuses = () => onStatusChange([]);
  const clearTeams = () => onTeamChange([]);
  const clearTaskTypes = () => onTaskTypeChange?.([]);

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex flex-col sm:flex-row gap-4">
          <div className="flex-1">
            <Input
              placeholder="Search tasks..."
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            {/* Status Multi-Select */}
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" className="min-w-32 justify-between">
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
              <PopoverTrigger asChild>
                <Button variant="outline" className="min-w-32 justify-between">
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
                <PopoverTrigger asChild>
                  <Button variant="outline" className="min-w-32 justify-between">
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
                        <span className="text-sm">{TASK_TYPE_LABELS[type]}</span>
                      </label>
                    ))}
                  </div>
                </PopoverContent>
              </Popover>
            )}
          </div>
        </div>

        {/* Active Filters */}
        {(statusFilter.length > 0 || teamFilter.length > 0 || taskTypeFilter.length > 0) && (
          <div className="flex flex-wrap gap-2 mt-3 pt-3 border-t">
            {statusFilter.map((status) => (
              <Badge key={status} variant="secondary" className="gap-1">
                {STATUS_LABELS[status]}
                <X
                  className="h-3 w-3 cursor-pointer hover:text-destructive"
                  onClick={() => toggleStatus(status)}
                />
              </Badge>
            ))}
            {teamFilter.map((team) => (
              <Badge key={team} variant="secondary" className="gap-1">
                {TEAM_LABELS[team]}
                <X
                  className="h-3 w-3 cursor-pointer hover:text-destructive"
                  onClick={() => toggleTeam(team)}
                />
              </Badge>
            ))}
            {taskTypeFilter.map((type) => (
              <Badge key={type} variant="secondary" className="gap-1">
                {TASK_TYPE_LABELS[type]}
                <X
                  className="h-3 w-3 cursor-pointer hover:text-destructive"
                  onClick={() => toggleTaskType(type)}
                />
              </Badge>
            ))}
            {(statusFilter.length > 0 || teamFilter.length > 0 || taskTypeFilter.length > 0) && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs"
                onClick={() => {
                  clearStatuses();
                  clearTeams();
                  clearTaskTypes();
                }}
              >
                Clear all
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
