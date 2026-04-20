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
import { Team } from "@/types";

interface ProjectFiltersProps {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  cellFilter: Team[];
  onCellChange: (value: Team[]) => void;
  showInactive: boolean;
  onShowInactiveChange: (value: boolean) => void;
}

const cells: { value: Team; label: string }[] = [
  { value: Team.BACKEND, label: "Backend" },
  { value: Team.FRONTEND, label: "Frontend" },
  { value: Team.UX_UI, label: "UX/UI" },
];

export function ProjectFilters({
  searchQuery,
  onSearchChange,
  cellFilter,
  onCellChange,
  showInactive,
  onShowInactiveChange,
}: ProjectFiltersProps) {
  const handleCellToggle = (cell: Team) => {
    if (cellFilter.includes(cell)) {
      onCellChange(cellFilter.filter((c) => c !== cell));
    } else {
      onCellChange([...cellFilter, cell]);
    }
  };

  const clearFilters = () => {
    onSearchChange("");
    onCellChange([]);
    onShowInactiveChange(false);
  };

  const hasActiveFilters = searchQuery || cellFilter.length > 0 || showInactive;

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
      {/* Search */}
      <div className="relative flex-1 max-w-sm">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          placeholder="Search projects..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          className="pl-9"
        />
      </div>

      {/* Cell Filter */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" className="gap-2">
            <Filter className="h-4 w-4" />
            Cell
            {cellFilter.length > 0 && (
              <Badge variant="secondary" className="ml-1">
                {cellFilter.length}
              </Badge>
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-48">
          <DropdownMenuLabel>Filter by Cell</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {cells.map((cell) => (
            <DropdownMenuCheckboxItem
              key={cell.value}
              checked={cellFilter.includes(cell.value)}
              onCheckedChange={() => handleCellToggle(cell.value)}
            >
              {cell.label}
            </DropdownMenuCheckboxItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Show Inactive Toggle */}
      <Button
        variant={showInactive ? "default" : "outline"}
        onClick={() => onShowInactiveChange(!showInactive)}
        className="gap-2"
      >
        {showInactive ? "Showing Inactive" : "Show Inactive"}
      </Button>

      {/* Clear Filters */}
      {hasActiveFilters && (
        <Button variant="ghost" onClick={clearFilters} className="gap-2">
          <X className="h-4 w-4" />
          Clear
        </Button>
      )}
    </div>
  );
}
