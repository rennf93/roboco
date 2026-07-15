"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import {
  getAgentDisplayName,
  getAgentInitials,
  getAgentTeamColor,
  TEAM_COLOR_CLASSES,
} from "@/lib/agent-utils";
import { useTask } from "@/hooks/use-tasks";
import { cn } from "@/lib/utils";
import { ListTodo, Users } from "lucide-react";

/** One participant's identity card — avatar, name, team badge — linking to
 * the agent's own detail page (design doc §1/§2). Read-only, same team-color
 * mapping every other identity affordance uses. */
function IdentityCard({ slug }: { slug: string }) {
  const teamColor = getAgentTeamColor(slug);
  return (
    <Link
      href={`/agents/${slug}`}
      className="flex items-center gap-2 rounded-lg border p-2 hover:bg-muted/50 transition-colors"
    >
      <HelpTip label={getAgentDisplayName(slug)}>
        <div
          className={cn(
            "h-9 w-9 rounded-full border flex items-center justify-center shrink-0",
            TEAM_COLOR_CLASSES[teamColor],
          )}
        >
          <span className="text-[10px] font-bold tracking-tight">
            {getAgentInitials(slug)}
          </span>
        </div>
      </HelpTip>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium truncate">
          {getAgentDisplayName(slug)}
        </div>
        <HelpTip label="Team this agent belongs to in the org hierarchy">
          <Badge
            variant="outline"
            className={cn("text-[10px] mt-0.5 w-fit", TEAM_COLOR_CLASSES[teamColor])}
          >
            {teamColor.replace("_", "/")}
          </Badge>
        </HelpTip>
      </div>
    </Link>
  );
}

interface A2AContextPaneProps {
  agentA: string;
  agentB: string;
  /** null when this conversation (or peeked pair) has no linked task. */
  taskId: string | null;
}

/** The `xl:`+ context region: both participants' identity cards, a linked-task
 * summary, and a no-task hint when there isn't one — read-only, never a
 * second place to act on the conversation (design doc §1). */
export function A2AContextPane({
  agentA,
  agentB,
  taskId,
}: A2AContextPaneProps) {
  const { data: task, isLoading } = useTask(taskId ?? "");

  return (
    <div className="p-3 space-y-4">
      <HelpTip label="Read-only: participant identities and the conversation's linked task">
        <div className="flex items-center gap-2 pb-2 border-b w-fit">
          <Users className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">Context</span>
        </div>
      </HelpTip>

      <div className="space-y-2">
        <IdentityCard slug={agentA} />
        <IdentityCard slug={agentB} />
      </div>

      <div className="space-y-2">
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Linked task
        </div>
        {!taskId ? (
          <p className="text-xs text-muted-foreground">
            This conversation has no linked task
          </p>
        ) : isLoading || !task ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <div className="rounded-lg border p-2.5 space-y-1.5">
            <div className="text-sm font-medium truncate">{task.title}</div>
            <div className="flex items-center justify-between gap-2">
              <HelpTip label="Current lifecycle status of the linked task">
                <Badge
                  variant={task.status === "completed" ? "default" : "secondary"}
                  className="text-xs w-fit"
                >
                  {task.status}
                </Badge>
              </HelpTip>
              <HelpTip label="Opens this task's full detail page">
                <Link
                  prefetch={false}
                  href={`/tasks/${taskId}`}
                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                >
                  <ListTodo className="h-3 w-3" />
                  View task
                </Link>
              </HelpTip>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
