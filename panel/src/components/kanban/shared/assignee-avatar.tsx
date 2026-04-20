"use client";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { getAgentInitials } from "@/lib/agent-utils";

interface AssigneeAvatarProps {
  agentId: string | null;
  size?: "sm" | "md";
}

export function AssigneeAvatar({ agentId, size = "sm" }: AssigneeAvatarProps) {
  if (!agentId) return null;

  const initials = getAgentInitials(agentId);
  const sizeClasses = size === "sm" ? "h-6 w-6 text-xs" : "h-8 w-8 text-sm";

  return (
    <Avatar className={sizeClasses}>
      <AvatarFallback className="bg-primary/10 text-primary">
        {initials}
      </AvatarFallback>
    </Avatar>
  );
}
