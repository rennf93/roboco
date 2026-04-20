"use client";

import { useState } from "react";
import { useTasks } from "@/hooks/use-tasks";
import { TaskStatus } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Search, X, Link2, Check } from "lucide-react";

interface DependencySelectorProps {
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  excludeTaskId?: string;
}

export function DependencySelector({
  selectedIds,
  onChange,
  excludeTaskId,
}: DependencySelectorProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const { data: tasks } = useTasks();

  // Filter available tasks (exclude completed, cancelled, and current task)
  const availableTasks = (tasks ?? []).filter(
    (task) =>
      task.id !== excludeTaskId &&
      task.status !== TaskStatus.COMPLETED &&
      task.status !== TaskStatus.CANCELLED
  );

  // Filter by search term
  const filteredTasks = availableTasks.filter(
    (task) =>
      task.title.toLowerCase().includes(search.toLowerCase()) ||
      task.id.toLowerCase().includes(search.toLowerCase())
  );

  // Get selected task objects
  const selectedTasks = (tasks ?? []).filter((t) => selectedIds.includes(t.id));

  const toggleTask = (taskId: string) => {
    if (selectedIds.includes(taskId)) {
      onChange(selectedIds.filter((id) => id !== taskId));
    } else {
      onChange([...selectedIds, taskId]);
    }
  };

  const removeTask = (taskId: string) => {
    onChange(selectedIds.filter((id) => id !== taskId));
  };

  return (
    <div className="space-y-2">
      <Label>Dependencies (optional)</Label>

      {/* Selected dependencies */}
      {selectedTasks.length > 0 && (
        <div className="space-y-2 border rounded-lg p-3 bg-muted/30">
          {selectedTasks.map((task) => (
            <div key={task.id} className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <Link2 className="h-4 w-4 text-muted-foreground shrink-0" />
                <span className="text-sm truncate">{task.title}</span>
                <Badge variant="outline" className="font-mono text-xs shrink-0">
                  {task.id.slice(0, 8)}
                </Badge>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-6 w-6 shrink-0"
                onClick={() => removeTask(task.id)}
              >
                <X className="h-3 w-3" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Add dependency popover */}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button type="button" variant="outline" className="w-full justify-start">
            <Search className="h-4 w-4 mr-2" />
            Search tasks to add as dependencies...
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-80 sm:w-96 p-0" align="start">
          <div className="p-2 border-b">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by title or ID..."
              className="h-8"
              autoFocus
            />
          </div>
          <ScrollArea className="h-[240px]">
            {filteredTasks.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">
                No tasks found
              </p>
            ) : (
              <div className="p-1">
                {filteredTasks.map((task) => {
                  const isSelected = selectedIds.includes(task.id);
                  return (
                    <button
                      key={task.id}
                      type="button"
                      className={`w-full flex items-center gap-2 p-2 rounded-md text-left hover:bg-muted transition-colors ${
                        isSelected ? "bg-primary/10" : ""
                      }`}
                      onClick={() => toggleTask(task.id)}
                    >
                      <div
                        className={`h-4 w-4 rounded border flex items-center justify-center shrink-0 ${
                          isSelected ? "bg-primary border-primary" : "border-muted-foreground"
                        }`}
                      >
                        {isSelected && <Check className="h-3 w-3 text-primary-foreground" />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm truncate">{task.title}</p>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Badge variant="outline" className="font-mono text-xs">
                            {task.id.slice(0, 8)}
                          </Badge>
                          <span className="capitalize">{task.status.replace(/_/g, " ")}</span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </ScrollArea>
        </PopoverContent>
      </Popover>

      <p className="text-xs text-muted-foreground">
        Select tasks that must be completed before this task can start.
      </p>
    </div>
  );
}
