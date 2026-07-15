"use client";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { Search, Filter, X } from "lucide-react";
import { WorkSessionStatus } from "@/types";
import { HelpTip } from "@/components/ui/help-tip";

interface WorkSessionFiltersProps {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  statusFilter: WorkSessionStatus[];
  onStatusChange: (value: WorkSessionStatus[]) => void;
}

const statuses: { value: WorkSessionStatus; label: string; hint: string }[] = [
  {
    value: WorkSessionStatus.ACTIVE,
    label: "Active",
    hint: "An agent is currently working this branch.",
  },
  {
    value: WorkSessionStatus.COMPLETED,
    label: "Completed",
    hint: "The session's PR was merged — the branch's work is done.",
  },
  {
    value: WorkSessionStatus.ABANDONED,
    label: "Abandoned",
    hint: "Superseded by a re-claim, cancellation, or project deletion — never an auto-timeout.",
  },
];

export function WorkSessionFilters({
  searchQuery,
  onSearchChange,
  statusFilter,
  onStatusChange,
}: WorkSessionFiltersProps) {
  const handleStatusToggle = (status: WorkSessionStatus) => {
    if (statusFilter.includes(status)) {
      onStatusChange(statusFilter.filter((s) => s !== status));
    } else {
      onStatusChange([...statusFilter, status]);
    }
  };

  const clearFilters = () => {
    onSearchChange("");
    onStatusChange([]);
  };

  const hasActiveFilters = searchQuery || statusFilter.length > 0;

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
      {/* Search */}
      <HelpTip label="Filters the table client-side by branch name substring — doesn't re-query the server.">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search by branch name..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="pl-9"
          />
        </div>
      </HelpTip>

      {/* Status Filter */}
      <DropdownMenu>
        {/* DropdownMenuTrigger's asChild is safe to wrap directly (unlike
            TabsTrigger/CollapsibleTrigger) — it carries no persistent visual
            data-state of its own. */}
        <HelpTip label="Show only sessions in the checked status(es). No selection shows every session.">
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="gap-2">
              <Filter className="h-4 w-4" />
              Status
              {statusFilter.length > 0 && (
                <Badge variant="secondary" className="ml-1">
                  {statusFilter.length}
                </Badge>
              )}
            </Button>
          </DropdownMenuTrigger>
        </HelpTip>
        <DropdownMenuContent align="start" className="w-48">
          <DropdownMenuLabel>Filter by Status</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {statuses.map((status) => (
            <DropdownMenuCheckboxItem
              key={status.value}
              checked={statusFilter.includes(status.value)}
              onCheckedChange={() => handleStatusToggle(status.value)}
            >
              <HelpTip label={status.hint}>
                <span className="w-fit">{status.label}</span>
              </HelpTip>
            </DropdownMenuCheckboxItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Clear Filters */}
      {hasActiveFilters && (
        <HelpTip label="Resets the search text and status filter — shows every work session again.">
          <Button variant="ghost" onClick={clearFilters} className="gap-2">
            <X className="h-4 w-4" />
            Clear
          </Button>
        </HelpTip>
      )}
    </div>
  );
}
