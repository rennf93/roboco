"use client";

import { Card } from "@/components/ui/card";
import { HelpTip } from "@/components/ui/help-tip";
import { cn } from "@/lib/utils";

export interface TaskStatusTileData {
  label: string;
  value: number;
  icon: React.ReactNode;
  /** What this status means — shown on hover. Omit for a self-explanatory label. */
  tip?: string;
}

interface TaskStatusTilesProps {
  tiles: TaskStatusTileData[];
  className?: string;
}

/**
 * Compact grid of task-status counts (label + number + status-colored icon
 * per tile) — replaces five full-size stat cards that sat mostly empty next
 * to the status donut (CEO feedback: "much smaller cards 2x3 or something").
 */
export function TaskStatusTiles({ tiles, className }: TaskStatusTilesProps) {
  return (
    <div className={cn("grid grid-cols-2 sm:grid-cols-3 gap-2", className)}>
      {tiles.map((tile) => (
        <HelpTip key={tile.label} label={tile.tip}>
          <Card className="gap-1 py-3">
            <div className="flex items-center gap-1.5 px-3 text-xs text-muted-foreground">
              {tile.icon}
              <span className="truncate">{tile.label}</span>
            </div>
            <div className="px-3 text-xl font-bold">{tile.value}</div>
          </Card>
        </HelpTip>
      ))}
    </div>
  );
}
