"use client";

import { Badge } from "@/components/ui/badge";

interface PriorityIndicatorProps {
  priority: number;
}

const priorityColors: Record<number, string> = {
  0: "bg-red-200 text-red-700",
  1: "bg-orange-200 text-orange-700",
  2: "bg-blue-200 text-blue-700",
  3: "bg-gray-200 text-gray-700",
};

const priorityLabels: Record<number, string> = {
  0: "P0",
  1: "P1",
  2: "P2",
  3: "P3",
};

export function PriorityIndicator({ priority }: PriorityIndicatorProps) {
  return (
    <Badge className={priorityColors[priority] ?? priorityColors[2] + " text-xs"}>
      {priorityLabels[priority] ?? "P2"}
    </Badge>
  );
}
