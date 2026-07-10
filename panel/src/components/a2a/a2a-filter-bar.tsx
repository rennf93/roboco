"use client";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { SlidersHorizontal, X } from "lucide-react";
import { getAgentDisplayName } from "@/lib/agent-utils";
import {
  EMPTY_A2A_FILTERS,
  activeA2AFilterCount,
  type A2AConversationStatus,
  type A2AFilters,
} from "./a2a-filter-utils";

const STATUS_OPTIONS: { value: A2AConversationStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
];

interface A2AFilterBarProps {
  filters: A2AFilters;
  onFiltersChange: (filters: A2AFilters) => void;
  /** Distinct agent slugs to list as checkboxes (already deduped+sorted —
   * see `distinctA2AAgents`). */
  agentOptions: string[];
  /** Task/Status/Date only narrow the List view — Switchboard pairs have no
   * conversation to filter those dimensions on (design doc §1). */
  view: "switchboard" | "list";
}

interface FilterChip {
  key: string;
  label: string;
  onRemove: () => void;
}

/**
 * Popover-triggered filter panel above the switchboard/list content: Agent
 * (multi-select), Task (id fragment + "No linked task"), Status (toggle
 * buttons) and a date range — plus the active-filter chip row and
 * Clear-all. See docs/ux_ui/design/conversations-filter-control.md.
 */
export function A2AFilterBar({
  filters,
  onFiltersChange,
  agentOptions,
  view,
}: A2AFilterBarProps) {
  const toggleAgent = (agent: string) => {
    onFiltersChange({
      ...filters,
      agents: filters.agents.includes(agent)
        ? filters.agents.filter((a) => a !== agent)
        : [...filters.agents, agent],
    });
  };

  const toggleStatus = (status: A2AConversationStatus) => {
    onFiltersChange({
      ...filters,
      statuses: filters.statuses.includes(status)
        ? filters.statuses.filter((s) => s !== status)
        : [...filters.statuses, status],
    });
  };

  const clearAll = () => onFiltersChange(EMPTY_A2A_FILTERS);

  const chips: FilterChip[] = [
    ...filters.agents.map((agent) => ({
      key: `agent-${agent}`,
      label: getAgentDisplayName(agent),
      onRemove: () => toggleAgent(agent),
    })),
    ...(filters.taskIdFragment
      ? [
          {
            key: "task-fragment",
            label: `Task: ${filters.taskIdFragment}`,
            onRemove: () => onFiltersChange({ ...filters, taskIdFragment: "" }),
          },
        ]
      : []),
    ...(filters.noLinkedTask
      ? [
          {
            key: "no-linked-task",
            label: "No linked task",
            onRemove: () =>
              onFiltersChange({ ...filters, noLinkedTask: false }),
          },
        ]
      : []),
    ...filters.statuses.map((status) => ({
      key: `status-${status}`,
      label: STATUS_OPTIONS.find((o) => o.value === status)?.label ?? status,
      onRemove: () => toggleStatus(status),
    })),
    ...(filters.dateFrom
      ? [
          {
            key: "date-from",
            label: `From ${filters.dateFrom}`,
            onRemove: () => onFiltersChange({ ...filters, dateFrom: "" }),
          },
        ]
      : []),
    ...(filters.dateTo
      ? [
          {
            key: "date-to",
            label: `To ${filters.dateTo}`,
            onRemove: () => onFiltersChange({ ...filters, dateTo: "" }),
          },
        ]
      : []),
  ];

  const count = activeA2AFilterCount(filters);

  return (
    <div className="mb-2 shrink-0">
      <div className="flex items-center justify-end">
        <Popover>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1 px-2 text-xs"
            >
              <SlidersHorizontal className="h-3.5 w-3.5" />
              {count > 0 ? `Filters · ${count}` : "Filters"}
            </Button>
          </PopoverTrigger>
          <PopoverContent
            align="end"
            className="w-72 max-h-[70vh] space-y-3 overflow-y-auto"
          >
            {view === "switchboard" && (
              <p className="text-xs text-muted-foreground">
                Task, Status, and Date filters apply to the Conversation List
                view.
              </p>
            )}

            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm font-medium">Agent</span>
                {filters.agents.length > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={() => onFiltersChange({ ...filters, agents: [] })}
                  >
                    Clear
                  </Button>
                )}
              </div>
              <div className="max-h-40 space-y-1 overflow-y-auto">
                {agentOptions.map((agent) => (
                  <label
                    key={agent}
                    className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 hover:bg-muted"
                  >
                    <Checkbox
                      checked={filters.agents.includes(agent)}
                      onCheckedChange={() => toggleAgent(agent)}
                    />
                    <span className="text-sm">
                      {getAgentDisplayName(agent)}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <div className="border-t pt-2">
              <span className="text-sm font-medium">Task</span>
              <Input
                value={filters.taskIdFragment}
                onChange={(e) =>
                  onFiltersChange({
                    ...filters,
                    taskIdFragment: e.target.value,
                  })
                }
                placeholder="Task id fragment..."
                className="mt-1 h-7 text-xs"
                aria-label="Task id fragment"
              />
              <label className="mt-2 flex cursor-pointer items-center gap-2">
                <Checkbox
                  checked={filters.noLinkedTask}
                  onCheckedChange={(checked) =>
                    onFiltersChange({
                      ...filters,
                      noLinkedTask: checked === true,
                    })
                  }
                />
                <span className="text-sm">No linked task</span>
              </label>
            </div>

            <div className="border-t pt-2">
              <span className="text-sm font-medium">Status</span>
              <div className="mt-1 flex items-center gap-1">
                {STATUS_OPTIONS.map((opt) => (
                  <Button
                    key={opt.value}
                    type="button"
                    variant={
                      filters.statuses.includes(opt.value)
                        ? "secondary"
                        : "outline"
                    }
                    size="sm"
                    className="h-7 px-2 text-xs"
                    aria-pressed={filters.statuses.includes(opt.value)}
                    onClick={() => toggleStatus(opt.value)}
                  >
                    {opt.label}
                  </Button>
                ))}
              </div>
            </div>

            <div className="border-t pt-2">
              <span className="text-sm font-medium">Date range</span>
              <div className="mt-1 flex items-center gap-2">
                <Input
                  type="date"
                  value={filters.dateFrom}
                  onChange={(e) =>
                    onFiltersChange({ ...filters, dateFrom: e.target.value })
                  }
                  className="h-7 text-xs"
                  aria-label="From date"
                />
                <Input
                  type="date"
                  value={filters.dateTo}
                  onChange={(e) =>
                    onFiltersChange({ ...filters, dateTo: e.target.value })
                  }
                  className="h-7 text-xs"
                  aria-label="To date"
                />
              </div>
            </div>

            {count > 0 && (
              <div className="flex justify-end border-t pt-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={clearAll}
                >
                  Clear all
                </Button>
              </div>
            )}
          </PopoverContent>
        </Popover>
      </div>

      {chips.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {chips.map((chip) => (
            <Badge key={chip.key} variant="secondary" className="gap-1">
              {chip.label}
              <button
                type="button"
                aria-label={`Remove ${chip.label} filter`}
                onClick={chip.onRemove}
              >
                <X className="h-3 w-3 cursor-pointer hover:text-destructive" />
              </button>
            </Badge>
          ))}
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={clearAll}
          >
            Clear all
          </Button>
        </div>
      )}
    </div>
  );
}
