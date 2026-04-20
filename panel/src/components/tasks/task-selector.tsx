"use client";

import { useMemo } from "react";
import { useTasks } from "@/hooks/use-tasks";
import { TaskStatus, Team } from "@/types";
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
import { ListTree, X } from "lucide-react";

interface TaskSelectorProps {
  value: string | null;
  onChange: (value: string | null) => void;
  placeholder?: string;
  filterByTeam?: Team;
  excludeTaskId?: string; // Exclude this task (useful when editing to prevent self-reference)
  disabled?: boolean;
  allowClear?: boolean;
}

// Status colors for badges
const STATUS_COLORS: Partial<Record<TaskStatus, string>> = {
  [TaskStatus.BACKLOG]: "bg-slate-100 text-slate-600",
  [TaskStatus.PENDING]: "bg-gray-100 text-gray-700",
  [TaskStatus.IN_PROGRESS]: "bg-blue-100 text-blue-700",
  [TaskStatus.COMPLETED]: "bg-green-100 text-green-700",
};

export function TaskSelector({
  value,
  onChange,
  placeholder = "Select parent task...",
  filterByTeam,
  excludeTaskId,
  disabled = false,
  allowClear = true,
}: TaskSelectorProps) {
  const { data: tasks = [], isLoading } = useTasks();

  // Filter and group tasks
  const filteredTasks = useMemo(() => {
    let filtered = tasks;

    // Exclude the task itself (to prevent circular reference)
    if (excludeTaskId) {
      filtered = filtered.filter((t) => t.id !== excludeTaskId);
    }

    // Filter by team if specified
    if (filterByTeam) {
      filtered = filtered.filter((t) => t.team === filterByTeam);
    }

    // Only show tasks that can be parents (not cancelled, not subtasks themselves deeply)
    filtered = filtered.filter(
      (t) => t.status !== TaskStatus.CANCELLED
    );

    // Sort by recency
    return filtered.sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );
  }, [tasks, excludeTaskId, filterByTeam]);

  // Group by team
  const groupedTasks = useMemo(() => {
    const groups: Record<string, typeof filteredTasks> = {
      board: [],
      main_pm: [],
      backend: [],
      frontend: [],
      ux_ui: [],
      marketing: [],
    };

    for (const task of filteredTasks) {
      if (task.team === Team.BOARD) {
        groups.board.push(task);
      } else if (task.team === Team.MAIN_PM) {
        groups.main_pm.push(task);
      } else if (task.team === Team.BACKEND) {
        groups.backend.push(task);
      } else if (task.team === Team.FRONTEND) {
        groups.frontend.push(task);
      } else if (task.team === Team.UX_UI) {
        groups.ux_ui.push(task);
      } else if (task.team === Team.MARKETING) {
        groups.marketing.push(task);
      } else {
        groups.board.push(task); // Default to board for unknown teams
      }
    }

    return groups;
  }, [filteredTasks]);

  // Find selected task for display
  const selectedTask = useMemo(() => {
    if (!value) return null;
    return tasks.find((t) => t.id === value);
  }, [tasks, value]);

  const handleValueChange = (newValue: string) => {
    if (newValue === "__clear__") {
      onChange(null);
    } else {
      onChange(newValue);
    }
  };

  const truncateTitle = (title: string, maxLen = 40) => {
    if (title.length <= maxLen) return title;
    return title.slice(0, maxLen) + "...";
  };

  return (
    <Select
      value={value || ""}
      onValueChange={handleValueChange}
      disabled={disabled || isLoading}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder}>
          {selectedTask ? (
            <div className="flex items-center gap-2 overflow-hidden">
              <ListTree className="h-4 w-4 shrink-0" />
              <span className="truncate">{truncateTitle(selectedTask.title)}</span>
            </div>
          ) : (
            placeholder
          )}
        </SelectValue>
      </SelectTrigger>
      <SelectContent className="max-h-[300px]">
        {allowClear && value && (
          <SelectItem value="__clear__" className="text-muted-foreground">
            <span className="flex items-center gap-2">
              <X className="h-4 w-4" />
              No parent (standalone task)
            </span>
          </SelectItem>
        )}

        {/* Board Tasks */}
        {groupedTasks.board.length > 0 && (
          <SelectGroup>
            <SelectLabel>Board</SelectLabel>
            {groupedTasks.board.slice(0, 10).map((task) => (
              <SelectItem key={task.id} value={task.id}>
                <div className="flex items-center gap-2">
                  <span className="truncate">{truncateTitle(task.title, 30)}</span>
                  <Badge
                    variant="secondary"
                    className={`text-xs ${STATUS_COLORS[task.status] || ""}`}
                  >
                    {task.status.replace(/_/g, " ")}
                  </Badge>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Main PM Tasks */}
        {groupedTasks.main_pm.length > 0 && (
          <SelectGroup>
            <SelectLabel>Main PM</SelectLabel>
            {groupedTasks.main_pm.slice(0, 10).map((task) => (
              <SelectItem key={task.id} value={task.id}>
                <div className="flex items-center gap-2">
                  <span className="truncate">{truncateTitle(task.title, 30)}</span>
                  <Badge
                    variant="secondary"
                    className={`text-xs ${STATUS_COLORS[task.status] || ""}`}
                  >
                    {task.status.replace(/_/g, " ")}
                  </Badge>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Backend Tasks */}
        {groupedTasks.backend.length > 0 && (
          <SelectGroup>
            <SelectLabel>Backend</SelectLabel>
            {groupedTasks.backend.slice(0, 10).map((task) => (
              <SelectItem key={task.id} value={task.id}>
                <div className="flex items-center gap-2">
                  <span className="truncate">{truncateTitle(task.title, 30)}</span>
                  <Badge
                    variant="secondary"
                    className={`text-xs ${STATUS_COLORS[task.status] || ""}`}
                  >
                    {task.status.replace(/_/g, " ")}
                  </Badge>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Frontend Tasks */}
        {groupedTasks.frontend.length > 0 && (
          <SelectGroup>
            <SelectLabel>Frontend</SelectLabel>
            {groupedTasks.frontend.slice(0, 10).map((task) => (
              <SelectItem key={task.id} value={task.id}>
                <div className="flex items-center gap-2">
                  <span className="truncate">{truncateTitle(task.title, 30)}</span>
                  <Badge
                    variant="secondary"
                    className={`text-xs ${STATUS_COLORS[task.status] || ""}`}
                  >
                    {task.status.replace(/_/g, " ")}
                  </Badge>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* UX/UI Tasks */}
        {groupedTasks.ux_ui.length > 0 && (
          <SelectGroup>
            <SelectLabel>UX/UI</SelectLabel>
            {groupedTasks.ux_ui.slice(0, 10).map((task) => (
              <SelectItem key={task.id} value={task.id}>
                <div className="flex items-center gap-2">
                  <span className="truncate">{truncateTitle(task.title, 30)}</span>
                  <Badge
                    variant="secondary"
                    className={`text-xs ${STATUS_COLORS[task.status] || ""}`}
                  >
                    {task.status.replace(/_/g, " ")}
                  </Badge>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {/* Marketing Tasks */}
        {groupedTasks.marketing.length > 0 && (
          <SelectGroup>
            <SelectLabel>Marketing</SelectLabel>
            {groupedTasks.marketing.slice(0, 10).map((task) => (
              <SelectItem key={task.id} value={task.id}>
                <div className="flex items-center gap-2">
                  <span className="truncate">{truncateTitle(task.title, 30)}</span>
                  <Badge
                    variant="secondary"
                    className={`text-xs ${STATUS_COLORS[task.status] || ""}`}
                  >
                    {task.status.replace(/_/g, " ")}
                  </Badge>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        )}

        {filteredTasks.length === 0 && (
          <div className="py-6 text-center text-sm text-muted-foreground">
            No tasks available
          </div>
        )}
      </SelectContent>
    </Select>
  );
}
