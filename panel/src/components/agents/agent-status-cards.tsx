import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { AgentStatusResponse } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HelpTip } from "@/components/ui/help-tip";
import { Activity, FileText, Clock, AlertCircle } from "lucide-react";
import { AgentStateBadge } from "./agent-state-badge";

interface AgentStatusCardsProps {
  agent: AgentStatusResponse;
}

export function AgentStatusCards({ agent }: AgentStatusCardsProps) {
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">State</CardTitle>
          <Activity className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <AgentStateBadge state={agent.state} size="lg" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Current Task</CardTitle>
          <FileText className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          {agent.task_id ? (
            <HelpTip label={`Full task id: ${agent.task_id}`}>
              <Link
                prefetch={false}
                href={"/tasks/" + agent.task_id}
                className="text-blue-500 hover:underline w-fit inline-block"
              >
                {agent.task_id.slice(0, 8)}...
              </Link>
            </HelpTip>
          ) : (
            <span className="text-muted-foreground">No task assigned</span>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Started At</CardTitle>
          <Clock className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          {agent.started_at ? (
            <HelpTip label={new Date(agent.started_at).toLocaleString()}>
              <span className="w-fit inline-block">
                {formatDistanceToNow(new Date(agent.started_at))} ago
              </span>
            </HelpTip>
          ) : (
            <span className="text-muted-foreground">Not started</span>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Error Count</CardTitle>
          <AlertCircle className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <HelpTip label="Errors this agent has hit since its current session started">
            <span
              className={
                "w-fit inline-block " +
                (agent.error_count > 0 ? "text-red-600 font-semibold" : "")
              }
            >
              {agent.error_count}
            </span>
          </HelpTip>
          {agent.waiting_for && (
            <HelpTip label="This agent is blocked here and needs human input to continue">
              <p className="text-xs text-yellow-600 mt-1 truncate w-fit">
                Waiting: {agent.waiting_for}
              </p>
            </HelpTip>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
