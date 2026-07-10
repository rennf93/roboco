"use client";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Search } from "lucide-react";
import type { A2AStatusFilter } from "./a2a-filter-utils";

interface A2AFilterBarProps {
  status: A2AStatusFilter;
  onStatusChange: (status: A2AStatusFilter) => void;
  search: string;
  onSearchChange: (value: string) => void;
}

/**
 * Filter bar above the switchboard/list content: free-text search (agent
 * name or topic) plus an active/all status toggle. Both narrow the
 * switchboard's pairs and the classic list's conversations identically —
 * see a2a-filter-utils.ts.
 */
export function A2AFilterBar({
  status,
  onStatusChange,
  search,
  onSearchChange,
}: A2AFilterBarProps) {
  return (
    <div className="flex items-center gap-2 mb-2 shrink-0">
      <div className="relative flex-1 min-w-0">
        <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search agent or topic..."
          className="h-7 pl-7 text-xs"
          aria-label="Search agent or topic"
        />
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <Button
          type="button"
          variant={status === "active" ? "secondary" : "ghost"}
          size="sm"
          className="h-7 px-2 text-xs"
          aria-pressed={status === "active"}
          onClick={() => onStatusChange("active")}
        >
          Active
        </Button>
        <Button
          type="button"
          variant={status === "all" ? "secondary" : "ghost"}
          size="sm"
          className="h-7 px-2 text-xs"
          aria-pressed={status === "all"}
          onClick={() => onStatusChange("all")}
        >
          All
        </Button>
      </div>
    </div>
  );
}
