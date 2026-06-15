"use client";

import { Badge } from "@/components/ui/badge";

interface PriorityIndicatorProps {
  priority: number;
}

const priorityColors: Record<number, string> = {
  0: "bg-red-200 text-red-700 text-xs",
  1: "bg-orange-200 text-orange-700 text-xs",
  2: "bg-blue-200 text-blue-700 text-xs",
  3: "bg-gray-200 text-gray-700 text-xs",
};

const priorityLabels: Record<number, string> = {
  0: "P0 - Highest",
  1: "P1 - High",
  2: "P2 - Medium",
  3: "P3 - Low",
};

export function PriorityIndicator({ priority }: PriorityIndicatorProps) {
  return (
    <Badge className={priorityColors[priority] ?? priorityColors[2]}>
      {priorityLabels[priority] ?? "P2 - Medium"}
    </Badge>
  );
}
