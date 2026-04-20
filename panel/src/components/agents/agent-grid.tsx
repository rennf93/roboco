import { AgentStatusResponse } from "@/types";
import { AgentDefinition } from "@/lib/agent-definitions";
import { Card, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentCard } from "./agent-card";

interface AgentGridProps {
  title: string;
  agents: AgentDefinition[];
  agentStatuses: Record<string, AgentStatusResponse>;
  isLoading: boolean;
  columns?: number;
}

export function AgentGrid({
  title,
  agents,
  agentStatuses,
  isLoading,
  columns = 4
}: AgentGridProps) {
  const gridCols = {
    3: "md:grid-cols-3",
    4: "md:grid-cols-3 lg:grid-cols-4",
    5: "md:grid-cols-3 lg:grid-cols-5",
  }[columns] || "md:grid-cols-3 lg:grid-cols-4";

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">{title}</h2>
      <div className={"grid gap-4 " + gridCols}>
        {isLoading ? (
          Array.from({ length: agents.length || 3 }).map((_, i) => (
            <Card key={i}>
              <CardHeader>
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-3 w-24" />
              </CardHeader>
            </Card>
          ))
        ) : (
          agents.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              agentStatus={agentStatuses[agent.id] || null}
            />
          ))
        )}
      </div>
    </div>
  );
}
