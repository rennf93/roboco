"use client";

import { Agent } from "@/types";
import { cn } from "@/lib/utils";
import { HelpTip } from "@/components/ui/help-tip";
import { getAgentDisplayName } from "@/lib/agent-utils";

interface AgentItemProps {
  agent: Agent;
  isSelected: boolean;
  onClick: () => void;
  hasEntries?: boolean;
}

// Soft per-team avatar tints so the list reads at a glance (works on light + dark).
const TEAM_AVATAR: Record<string, string> = {
  backend: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  frontend: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  ux_ui: "bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-400",
  board: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  main_pm: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  marketing: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
};

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function AgentItem({
  agent,
  isSelected,
  onClick,
  hasEntries,
}: AgentItemProps) {
  const name = getAgentDisplayName(agent.agent_id);
  const avatarTint =
    TEAM_AVATAR[agent.team ?? ""] ?? "bg-muted text-muted-foreground";

  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={isSelected ? "true" : undefined}
      className={cn(
        "group flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        isSelected
          ? "bg-primary/10 ring-1 ring-inset ring-primary/40"
          : "hover:bg-muted",
      )}
    >
      <div className="relative shrink-0">
        <HelpTip label={name}>
          <div
            className={cn(
              "flex h-9 w-9 items-center justify-center rounded-full text-xs font-semibold transition-colors",
              isSelected ? "bg-primary/15 text-primary" : avatarTint,
            )}
          >
            {initialsFor(name)}
          </div>
        </HelpTip>
        {hasEntries && (
          <HelpTip label="Has journal entries">
            <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-background" />
          </HelpTip>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "truncate text-sm font-medium",
            isSelected && "text-primary",
          )}
        >
          {name}
        </p>
        <p className="truncate text-xs capitalize text-muted-foreground">
          {agent.role.replace(/_/g, " ")}
        </p>
      </div>
    </button>
  );
}
