import { OrchestratorStatus as OrchestratorStatusType } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Server, Users, Clock, Activity } from "lucide-react";

interface OrchestratorStatusCardsProps {
  status: OrchestratorStatusType | undefined;
  isLoading: boolean;
  /** Full org roster size (from useAgentDefinitions), NOT the backend's
   * `status.total_agents` — that field is the orchestrator's live in-memory
   * instance count (roboco/runtime/orchestrator.py get_status_summary:
   * `len(self._instances)`), so it read 0 whenever nothing was spawned even
   * though the full 25-agent roster renders below. Total Agents must reflect
   * the roster, not who's currently running. */
  rosterCount: number;
  rosterLoading?: boolean;
}

/** One compact stat row instead of 4 separate full cards — same information,
 * a fraction of the chrome. */
export function OrchestratorStatusCards({
  status,
  isLoading,
  rosterCount,
  rosterLoading = false,
}: OrchestratorStatusCardsProps) {
  // The orchestrator's by_state keys are the real OrchestratorAgentState
  // values (offline/starting/active/waiting_short/waiting_long/idle/stopping)
  // — "running"/"ready" are not states it ever emits, so keying off those (as
  // this card previously did) always read 0. "active" is the one state the
  // orchestrator sets while a spawned agent is actually doing work.
  const activeCount = status?.by_state?.active ?? 0;
  const waitingCount = status?.waiting_count ?? 0;
  // The orchestrator service is up whenever its status query resolves — agent
  // count is shown separately in the cells below.
  const isRunning = status !== undefined;

  return (
    <Card className="py-0">
      <CardContent className="grid grid-cols-1 divide-y divide-border sm:grid-cols-4 sm:divide-x sm:divide-y-0">
        <div className="flex items-center justify-between gap-2 p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Server className="h-4 w-4" />
            Orchestrator
          </div>
          {isLoading ? (
            <Skeleton className="h-6 w-16" />
          ) : (
            <Badge
              data-testid="stat-orchestrator"
              className={isRunning ? "bg-green-500" : "bg-red-500"}
            >
              {isRunning ? "Running" : "Stopped"}
            </Badge>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Users className="h-4 w-4" />
            Total Agents
          </div>
          {rosterLoading ? (
            <Skeleton className="h-6 w-8" />
          ) : (
            <span data-testid="stat-total-agents" className="text-xl font-bold">
              {rosterCount}
            </span>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Activity className="h-4 w-4" />
            Active
          </div>
          {isLoading ? (
            <Skeleton className="h-6 w-8" />
          ) : (
            <span data-testid="stat-active" className="text-xl font-bold">
              {activeCount}
            </span>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Clock className="h-4 w-4" />
            Waiting
          </div>
          {isLoading ? (
            <Skeleton className="h-6 w-8" />
          ) : (
            <span data-testid="stat-waiting" className="text-xl font-bold">
              {waitingCount}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
