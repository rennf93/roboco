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

// Intrinsic sizing: every card gets at least 17rem and rows fill whatever
// the viewport offers — one column on a phone, eight on a 27" — with a
// consistent card width across all team sections at any width.
const GRID_COLS = "grid-cols-[repeat(auto-fill,minmax(17rem,1fr))]";

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
              <Card key={i} className="gap-2.5 py-4">
                <CardHeader className="gap-1 px-4">
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
