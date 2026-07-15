"use client";

import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";

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

const priorityDescriptions: Record<number, string> = {
  0: "Highest urgency — work this before anything else.",
  1: "High urgency — prioritize over P2/P3 work.",
  2: "Standard priority — the default for most tasks.",
  3: "Low urgency — fine to defer behind higher-priority work.",
};

export function PriorityIndicator({ priority }: PriorityIndicatorProps) {
  return (
    <HelpTip label={priorityDescriptions[priority] ?? priorityDescriptions[2]}>
      <Badge className={priorityColors[priority] ?? priorityColors[2]}>
        {priorityLabels[priority] ?? "P2 - Medium"}
      </Badge>
    </HelpTip>
  );
}
