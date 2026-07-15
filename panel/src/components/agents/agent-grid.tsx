import { AgentStatusResponse, AgentUsageRow } from "@/types";
import { AgentDefinition } from "@/lib/agent-definitions";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentCard } from "./agent-card";

interface AgentGridProps {
  title: string;
  agents: AgentDefinition[];
  agentStatuses: Record<string, AgentStatusResponse>;
  agentUsage?: Record<string, AgentUsageRow>;
  isLoading: boolean;
}

// Compact cards need far less width per card than the old large ones, so
// wide screens fit up to 8 across instead of wrapping a tall, ragged grid.
const GRID_COLS =
  "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-7 2xl:grid-cols-8";

export function AgentGrid({
  title,
  agents,
  agentStatuses,
  agentUsage,
  isLoading,
}: AgentGridProps) {
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </h2>
        <Badge variant="secondary" className="text-xs">
          {agents.length}
        </Badge>
      </div>
      <div className={"grid gap-3 " + GRID_COLS}>
        {isLoading
          ? Array.from({ length: agents.length || 3 }).map((_, i) => (
              <Card key={i} className="gap-2 py-3">
                <CardHeader className="gap-1 px-3">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-3 w-16" />
                </CardHeader>
              </Card>
            ))
          : agents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                agentStatus={agentStatuses[agent.id] || null}
                usageRow={agentUsage?.[agent.id] ?? null}
              />
            ))}
      </div>
    </div>
  );
}
