"use client";

import { Agent } from "@/types";
import { User } from "lucide-react";
import { cn } from "@/lib/utils";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { Button } from "@/components/ui/button";

interface AgentItemProps {
  agent: Agent;
  isSelected: boolean;
  onClick: () => void;
  hasEntries?: boolean;
}

export function AgentItem({ agent, isSelected, onClick, hasEntries }: AgentItemProps) {
  return (
    <Button
      onClick={onClick}
      variant="ghost"
      className={cn(
        "w-full h-auto justify-start gap-3 p-2 font-normal whitespace-normal",
        isSelected
          ? "bg-primary/10 border border-primary/30 hover:bg-primary/10"
          : "hover:bg-muted/50"
      )}
    >
      <div className="relative">
        <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center">
          <User className="h-4 w-4 text-muted-foreground" />
        </div>
        {hasEntries && (
          <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 bg-primary rounded-full border-2 border-background" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{getAgentDisplayName(agent.agent_id)}</p>
        <p className="text-xs text-muted-foreground capitalize">
          {agent.role.replace(/_/g, " ")}
        </p>
      </div>
    </Button>
  );
}
