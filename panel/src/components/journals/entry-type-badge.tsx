"use client";

import { JournalEntryType } from "@/types";
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";

interface EntryTypeBadgeProps {
  type: JournalEntryType;
}

const typeConfig: Record<
  JournalEntryType,
  { label: string; color: string; description: string }
> = {
  [JournalEntryType.TASK_REFLECTION]: {
    label: "Task Reflection",
    color: "bg-blue-100 text-blue-700",
    description: "A wrap-up reflection the agent wrote after finishing a task",
  },
  [JournalEntryType.DECISION_LOG]: {
    label: "Decision Log",
    color: "bg-purple-100 text-purple-700",
    description: "A record of a significant decision the agent made, and why",
  },
  [JournalEntryType.LEARNING]: {
    label: "Learning",
    color: "bg-green-100 text-green-700",
    description:
      "A lesson learned — broadcast to other agents as a knowledge-share notification",
  },
  [JournalEntryType.STRUGGLE]: {
    label: "Struggle",
    color: "bg-orange-100 text-orange-700",
    description: "A difficulty the agent hit; a later entry may mark it resolved",
  },
  [JournalEntryType.GENERAL]: {
    label: "Note",
    color: "bg-gray-100 text-gray-700",
    description: "A general note that doesn't fit the other entry types",
  },
};

export function EntryTypeBadge({ type }: EntryTypeBadgeProps) {
  const config = typeConfig[type] ?? typeConfig[JournalEntryType.GENERAL];
  return (
    <HelpTip label={config.description}>
      <Badge className={config.color + " text-xs"}>{config.label}</Badge>
    </HelpTip>
  );
}
