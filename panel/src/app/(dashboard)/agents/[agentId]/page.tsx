"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";
import { formatDistanceToNow } from "date-fns";
import {
  useAgentStatus,
  useStopAgent,
  useAgentDefinition,
} from "@/hooks/use-agents";
import { usePageRefresh } from "@/hooks";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArrowLeft,
  Play,
  Square,
  AlertTriangle,
  Clock,
  User,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import {
  AgentStatusCards,
  ResolveWaitDialog,
  AgentStreamViewer,
  SpawnAgentDialog,
  AgentActivityPanel,
} from "@/components/agents";

// Role display labels
const ROLE_LABELS: Record<string, string> = {
  ceo: "CEO",
  product_owner: "Product Owner",
  head_marketing: "Head of Marketing",
  auditor: "Auditor",
  main_pm: "Main PM",
  cell_pm: "Cell PM",
  developer: "Developer",
  qa: "QA Engineer",
  documenter: "Documenter",
};

// Team display labels
const TEAM_LABELS: Record<string, string> = {
  board: "Board",
  main_pm: "Main PM",
  backend: "Backend",
  frontend: "Frontend",
  ux_ui: "UX/UI",
  marketing: "Marketing",
};

export default function AgentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const agentId = params.agentId as string;

  const {
    data: agent,
    isLoading: isStatusLoading,
    error: statusError,
    refetch,
  } = useAgentStatus(agentId);
  const {
    data: definition,
    isLoading: isDefinitionLoading,
    error: definitionError,
  } = useAgentDefinition(agentId);

  const { register, unregister } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  const stopAgent = useStopAgent();

  // Get display values from definition or fallback
  const displayName = definition?.name || agentId;
  const roleLabel = definition?.role
    ? ROLE_LABELS[definition.role] || definition.role
    : null;
  const teamLabel = definition?.team
    ? TEAM_LABELS[definition.team] || definition.team
    : null;

  const handleStop = async (graceful: boolean) => {
    try {
      await stopAgent.mutateAsync({ agentId, graceful });
      toast.success(
        graceful ? "Agent stopping gracefully" : "Agent force stopped",
      );
    } catch {
      toast.error("Failed to stop agent");
    }
  };

  // Fatal only when the agent identity itself doesn't resolve (roster lookup
  // failed) — a genuinely invalid id. A live-status error (agent not running)
  // is a normal, expected state for a stopped agent and must not discard the
  // DB-backed content below (activity panel, header) — see isAgentDown.
  const isInvalidAgent = !!definitionError && !isDefinitionLoading;

  if (isInvalidAgent) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back
        </Button>
        <Card className="w-full max-w-lg mx-auto">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-red-500">
              <AlertTriangle className="h-5 w-5" />
              <span>Failed to load agent status</span>
            </div>
            <p className="text-muted-foreground mt-2">
              The agent may not be running or the ID is invalid.
            </p>
            <SpawnAgentDialog
              agentId={agentId}
              agentName={displayName}
              trigger={
                <Button className="mt-4">
                  <Play className="h-4 w-4 mr-2" />
                  Spawn Agent
                </Button>
              }
            />
          </CardContent>
        </Card>
      </div>
    );
  }

  // Live status unreachable (stopped/unreachable agent, 404 from the
  // orchestrator) but the agent identity is real — degrade in place instead
  // of replacing the page.
  const isAgentDown = !!statusError && !isStatusLoading;

  const isActive =
    agent &&
    ["running", "ready", "starting", "waiting_long"].includes(agent.state);
  const isWaiting = agent?.state === "waiting_long";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="ghost" onClick={() => router.back()}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back
          </Button>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-3xl font-bold tracking-tight">
                {displayName}
              </h1>
              {roleLabel && (
                <Badge variant="secondary" className="gap-1">
                  <User className="h-3 w-3" />
                  {roleLabel}
                </Badge>
              )}
              {teamLabel && (
                <Badge variant="outline" className="gap-1">
                  <Users className="h-3 w-3" />
                  {teamLabel}
                </Badge>
              )}
            </div>
            <p className="text-muted-foreground">
              {agentId !== displayName ? `@${agentId}` : "Agent Details"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isWaiting && <ResolveWaitDialog agentId={agentId} />}
          {isActive ? (
            <>
              <Button variant="outline" onClick={() => handleStop(true)}>
                <Square className="h-4 w-4 mr-2" />
                Stop
              </Button>
              <Button variant="destructive" onClick={() => handleStop(false)}>
                <Square className="h-4 w-4 mr-2" />
                Force Stop
              </Button>
            </>
          ) : (
            <SpawnAgentDialog
              agentId={agentId}
              agentName={displayName}
              trigger={
                <Button>
                  <Play className="h-4 w-4 mr-2" />
                  Spawn
                </Button>
              }
            />
          )}
        </div>
      </div>

      {/* Per-agent activity: token sparkline + work-session/journal timeline.
          History exists independent of live status, so render for any slug. */}
      <AgentActivityPanel agentSlug={agentId} agentUuid={definition?.uuid} />

      {isStatusLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardHeader>
                <Skeleton className="h-5 w-24" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-8 w-32" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : isAgentDown ? (
        <Card className="border-muted-foreground/30">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-muted-foreground">
              <AlertTriangle className="h-5 w-5" />
              <span>Not running</span>
            </div>
            <p className="text-muted-foreground mt-2">
              Live status is unavailable — this agent isn&apos;t currently
              active. Spawn it to see live state.
            </p>
            <SpawnAgentDialog
              agentId={agentId}
              agentName={displayName}
              trigger={
                <Button className="mt-4">
                  <Play className="h-4 w-4 mr-2" />
                  Spawn Agent
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : agent ? (
        <>
          {/* Status Cards */}
          <AgentStatusCards agent={agent} />

          {/* Started At */}
          {agent.started_at && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">Started</CardTitle>
              </CardHeader>
              <CardContent>
                <p>{new Date(agent.started_at).toLocaleString()}</p>
                <p className="text-muted-foreground text-sm">
                  {formatDistanceToNow(new Date(agent.started_at))} ago
                </p>
              </CardContent>
            </Card>
          )}

          {/* Error Count */}
          {agent.error_count > 0 && (
            <Card className="border-red-500/50">
              <CardHeader>
                <CardTitle className="text-red-500 flex items-center gap-2">
                  <AlertTriangle className="h-5 w-5" />
                  Errors Encountered
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-lg font-semibold text-red-600">
                  {agent.error_count} error(s)
                </p>
              </CardContent>
            </Card>
          )}

          {/* Waiting State Info */}
          {isWaiting && (
            <Card className="border-orange-500/50">
              <CardHeader>
                <CardTitle className="text-orange-500 flex items-center gap-2">
                  <Clock className="h-5 w-5" />
                  Agent Waiting for Input
                </CardTitle>
                <CardDescription>
                  This agent is blocked and waiting for human input or external
                  resolution.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-muted-foreground">
                  Use the &quot;Resolve Wait&quot; button above to provide the
                  information or decision the agent needs to continue execution.
                </p>
              </CardContent>
            </Card>
          )}

          {/* Agent Stream Viewer */}
          {isActive && (
            <AgentStreamViewer agentId={agentId} agentName={displayName} />
          )}
        </>
      ) : null}
    </div>
  );
}
