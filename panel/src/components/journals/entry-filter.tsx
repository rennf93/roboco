"use client";

import { JournalEntryType, Task } from "@/types";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Search, ListTodo } from "lucide-react";

interface EntryFilterProps {
  typeFilter: JournalEntryType | "all";
  onTypeChange: (type: JournalEntryType | "all") => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  taskFilter: string | null;
  onTaskChange: (taskId: string | null) => void;
  tasks?: Task[];
  tasksLoading?: boolean;
}

const TYPE_OPTIONS = [
  { value: "all", label: "All Types" },
  { value: JournalEntryType.TASK_REFLECTION, label: "Task Reflections" },
  { value: JournalEntryType.DECISION_LOG, label: "Decision Logs" },
  { value: JournalEntryType.LEARNING, label: "Learnings" },
  { value: JournalEntryType.STRUGGLE, label: "Struggles" },
  { value: JournalEntryType.GENERAL, label: "Notes" },
];

export function EntryFilter({
  typeFilter,
  onTypeChange,
  searchQuery,
  onSearchChange,
  taskFilter,
  onTaskChange,
  tasks = [],
  tasksLoading = false,
}: EntryFilterProps) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="relative flex-1 min-w-48">
        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search entries..."
          className="pl-9"
        />
      </div>
      <Select value={typeFilter} onValueChange={(v) => onTypeChange(v as JournalEntryType | "all")}>
        <SelectTrigger className="w-auto min-w-32 shrink-0">
          <SelectValue placeholder="Filter by type" />
        </SelectTrigger>
        <SelectContent>
          {TYPE_OPTIONS.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={taskFilter ?? "all"}
        onValueChange={(v) => onTaskChange(v === "all" ? null : v)}
      >
        <SelectTrigger className="w-auto min-w-40 shrink-0">
          <div className="flex items-center gap-2">
            <ListTodo className="h-4 w-4" />
            <SelectValue placeholder="Filter by task" />
          </div>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All Tasks</SelectItem>
          {tasksLoading ? (
            <SelectItem value="_loading" disabled>
              Loading...
            </SelectItem>
          ) : (
            tasks.map((task) => (
              <SelectItem key={task.id} value={task.id}>
                <span className="truncate max-w-48">{task.title}</span>
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
    </div>
  );
}
