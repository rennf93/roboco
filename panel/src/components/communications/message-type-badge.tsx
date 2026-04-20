"use client";

import { Badge } from "@/components/ui/badge";

interface MessageTypeBadgeProps {
  type: string;
}

const typeConfig: Record<string, { label: string; color: string }> = {
  reasoning: { label: "reasoning", color: "bg-blue-100 text-blue-700" },
  dialogue: { label: "dialogue", color: "bg-green-100 text-green-700" },
  decision: { label: "decision", color: "bg-purple-100 text-purple-700" },
  action: { label: "action", color: "bg-orange-100 text-orange-700" },
  blocker: { label: "blocker", color: "bg-red-100 text-red-700" },
  technical: { label: "technical", color: "bg-gray-100 text-gray-700" },
  general: { label: "general", color: "bg-gray-100 text-gray-700" },
};

export function MessageTypeBadge({ type }: MessageTypeBadgeProps) {
  const config = typeConfig[type] ?? typeConfig.general;
  return <Badge className={config.color + " text-xs"}>{config.label}</Badge>;
}
