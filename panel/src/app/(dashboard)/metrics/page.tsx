"use client";

import { useOrchestratorStatus } from "@/hooks/use-agents";
import { useTasks } from "@/hooks/use-tasks";
import { TaskStatus, Team } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { OfflineState } from "@/components/ui/offline-state";
import {
  Activity,
  TrendingUp,
  Clock,
  AlertTriangle,
  Users,
  CheckCircle,
  XCircle,
  RefreshCw,
  Zap,
  Timer,
} from "lucide-react";

interface MetricCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ReactNode;
  trend?: "up" | "down" | "neutral";
  trendValue?: string;
}

function MetricCard({ title, value, subtitle, icon, trend, trendValue }: MetricCardProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {subtitle && (
          <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>
        )}
        {trend && trendValue && (
          <div className={"flex items-center gap-1 mt-2 text-xs " + 
            (trend === "up" ? "text-green-600" : trend === "down" ? "text-red-600" : "text-gray-500")
          }>
            <TrendingUp className={"h-3 w-3 " + (trend === "down" ? "rotate-180" : "")} />
            {trendValue}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface TeamHealthCardProps {
  team: Team;
  activeTasks: number;
  blockedTasks: number;
  completedToday: number;
}

function TeamHealthCard({ team, activeTasks, blockedTasks, completedToday }: TeamHealthCardProps) {
  const healthScore = Math.max(0, 100 - (blockedTasks * 20));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium capitalize">
          {team.replace(/_/g, " ")} Cell
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2 mb-3">
          <Progress value={healthScore} className="flex-1" />
          <span className="text-sm font-medium">{healthScore}%</span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <div>
            <div className="font-semibold text-blue-600">{activeTasks}</div>
            <div className="text-muted-foreground">Active</div>
          </div>
          <div>
            <div className="font-semibold text-red-600">{blockedTasks}</div>
            <div className="text-muted-foreground">Blocked</div>
          </div>
          <div>
            <div className="font-semibold text-green-600">{completedToday}</div>
            <div className="text-muted-foreground">Done</div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function MetricsPage() {
  const { data: tasks, error: tasksError, refetch: refetchTasks } = useTasks();
  const { data: status, error: statusError, refetch: refetchStatus } = useOrchestratorStatus();

  const isOffline = (tasksError || statusError) && (
    tasksError?.message?.includes("Network Error") ||
    statusError?.message?.includes("Network Error")
  );

  const refetch = () => {
    refetchTasks();
    refetchStatus();
  };

  // Calculate metrics from local data
  const taskList = tasks || [];
  const agentList = status?.agents || [];

  // Velocity metrics
  const completedToday = taskList.filter((t) => {
    if (!t.completed_at) return false;
    const completed = new Date(t.completed_at);
    const today = new Date();
    return completed.toDateString() === today.toDateString();
  }).length;

  const completedThisWeek = taskList.filter((t) => {
    if (!t.completed_at) return false;
    const completed = new Date(t.completed_at);
    const weekAgo = new Date();
    weekAgo.setDate(weekAgo.getDate() - 7);
    return completed > weekAgo;
  }).length;

  // Task status counts
  const pending = taskList.filter((t) => t.status === TaskStatus.PENDING).length;
  const inProgress = taskList.filter((t) => t.status === TaskStatus.IN_PROGRESS).length;
  const blocked = taskList.filter((t) => t.status === TaskStatus.BLOCKED).length;
  const awaitingQa = taskList.filter((t) => t.status === TaskStatus.AWAITING_QA).length;
  const completed = taskList.filter((t) => t.status === TaskStatus.COMPLETED).length;

  // Agent counts (from by_state if available, or count from agents array)
  const runningAgents = status?.by_state?.running || agentList.filter((a) => a.state === "running").length;
  const idleAgents = status?.by_state?.idle || agentList.filter((a) => a.state === "idle" || a.state === "stopped").length;
  const waitingAgents = status?.waiting_count || agentList.filter((a) => a.state === "waiting_long").length;
  const errorAgents = status?.by_state?.error || agentList.filter((a) => a.state === "error").length;

  // Team metrics
  const teamMetrics = Object.values(Team).map((team) => {
    const teamTasks = taskList.filter((t) => t.team === team);
    return {
      team,
      activeTasks: teamTasks.filter((t) => 
        [TaskStatus.IN_PROGRESS, TaskStatus.CLAIMED].includes(t.status)
      ).length,
      blockedTasks: teamTasks.filter((t) => t.status === TaskStatus.BLOCKED).length,
      completedToday: teamTasks.filter((t) => {
        if (!t.completed_at) return false;
        const completed = new Date(t.completed_at);
        const today = new Date();
        return completed.toDateString() === today.toDateString();
      }).length,
    };
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Metrics</h1>
          <p className="text-muted-foreground">
            Performance analytics and operational insights
          </p>
        </div>
        <Button variant="outline" onClick={refetch}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      {isOffline ? (
        <OfflineState
          title="Cannot Load Metrics"
          description="Start the RoboCo orchestrator to view performance analytics."
          onRetry={refetch}
        />
      ) : (
        <>
          {/* Velocity Metrics */}
          <div>
            <h2 className="text-lg font-semibold mb-3">Velocity</h2>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <MetricCard
                title="Completed Today"
                value={completedToday}
                subtitle="Tasks finished"
                icon={<Zap className="h-4 w-4 text-green-500" />}
              />
              <MetricCard
                title="Completed This Week"
                value={completedThisWeek}
                subtitle="Rolling 7 days"
                icon={<TrendingUp className="h-4 w-4 text-blue-500" />}
              />
              <MetricCard
                title="Total Completed"
                value={completed}
                subtitle="All time"
                icon={<CheckCircle className="h-4 w-4 text-green-500" />}
              />
              <MetricCard
                title="Completion Rate"
                value={taskList.length > 0 ? Math.round((completed / taskList.length) * 100) + "%" : "0%"}
                subtitle="Of all tasks"
                icon={<Activity className="h-4 w-4 text-purple-500" />}
              />
            </div>
          </div>

          {/* Task Status */}
          <div>
            <h2 className="text-lg font-semibold mb-3">Task Status</h2>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
              <MetricCard
                title="Pending"
                value={pending}
                icon={<Clock className="h-4 w-4 text-gray-500" />}
              />
              <MetricCard
                title="In Progress"
                value={inProgress}
                icon={<Activity className="h-4 w-4 text-blue-500" />}
              />
              <MetricCard
                title="Blocked"
                value={blocked}
                icon={<AlertTriangle className="h-4 w-4 text-red-500" />}
              />
              <MetricCard
                title="Awaiting QA"
                value={awaitingQa}
                icon={<Timer className="h-4 w-4 text-yellow-500" />}
              />
              <MetricCard
                title="Completed"
                value={completed}
                icon={<CheckCircle className="h-4 w-4 text-green-500" />}
              />
            </div>
          </div>

          {/* Agent Status */}
          <div>
            <h2 className="text-lg font-semibold mb-3">Agent Status</h2>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <MetricCard
                title="Running"
                value={runningAgents}
                subtitle="Active agents"
                icon={<Users className="h-4 w-4 text-green-500" />}
              />
              <MetricCard
                title="Idle"
                value={idleAgents}
                subtitle="Available"
                icon={<Users className="h-4 w-4 text-gray-500" />}
              />
              <MetricCard
                title="Waiting"
                value={waitingAgents}
                subtitle="Needs input"
                icon={<Clock className="h-4 w-4 text-yellow-500" />}
              />
              <MetricCard
                title="Errors"
                value={errorAgents}
                subtitle="Failed agents"
                icon={<XCircle className="h-4 w-4 text-red-500" />}
              />
            </div>
          </div>

          {/* Team Health */}
          <div>
            <h2 className="text-lg font-semibold mb-3">Team Health</h2>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
              {teamMetrics.map((tm) => (
                <TeamHealthCard
                  key={tm.team}
                  team={tm.team}
                  activeTasks={tm.activeTasks}
                  blockedTasks={tm.blockedTasks}
                  completedToday={tm.completedToday}
                />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
