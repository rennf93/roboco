"use client";

import { JournalEntryType } from "@/types";
import { Badge } from "@/components/ui/badge";

interface EntryTypeBadgeProps {
  type: JournalEntryType;
}

const typeConfig: Record<JournalEntryType, { label: string; color: string }> = {
  [JournalEntryType.TASK_REFLECTION]: {
    label: "Task Reflection",
    color: "bg-blue-100 text-blue-700",
  },
  [JournalEntryType.DECISION_LOG]: {
    label: "Decision Log",
    color: "bg-purple-100 text-purple-700",
  },
  [JournalEntryType.LEARNING]: {
    label: "Learning",
    color: "bg-green-100 text-green-700",
  },
  [JournalEntryType.STRUGGLE]: {
    label: "Struggle",
    color: "bg-orange-100 text-orange-700",
  },
  [JournalEntryType.GENERAL]: {
    label: "Note",
    color: "bg-gray-100 text-gray-700",
  },
};

export function EntryTypeBadge({ type }: EntryTypeBadgeProps) {
  const config = typeConfig[type] ?? typeConfig[JournalEntryType.GENERAL];
  return <Badge className={config.color + " text-xs"}>{config.label}</Badge>;
}
