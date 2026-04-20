"use client";

import { CheckCircle, Play, Pause, AlertTriangle, User, Clock } from "lucide-react";
import { getAgentDisplayName } from "@/lib/agent-utils";

export interface Activity {
  id: string;
  agent_id: string;
  action: string;
  task_id?: string;
  task_title?: string;
  timestamp: string;
}

interface ActivityItemProps {
  activity: Activity;
}

const actionIcons: Record<string, React.ReactNode> = {
  completed: <CheckCircle className="h-4 w-4 text-green-500" />,
  started: <Play className="h-4 w-4 text-blue-500" />,
  paused: <Pause className="h-4 w-4 text-yellow-500" />,
  blocked: <AlertTriangle className="h-4 w-4 text-red-500" />,
  claimed: <User className="h-4 w-4 text-purple-500" />,
  passed_qa: <CheckCircle className="h-4 w-4 text-green-500" />,
  failed_qa: <AlertTriangle className="h-4 w-4 text-red-500" />,
};

const actionLabels: Record<string, string> = {
  completed: "completed",
  started: "started",
  paused: "paused",
  blocked: "blocked on",
  claimed: "claimed",
  passed_qa: "passed QA on",
  failed_qa: "failed QA on",
};

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function ActivityItem({ activity }: ActivityItemProps) {
  const action = activity.action || "unknown";
  const icon = actionIcons[action] || <Clock className="h-4 w-4" />;
  const label = actionLabels[action] || action;

  return (
    <div className="flex items-start gap-3 py-2">
      <div className="mt-0.5">{icon}</div>
      <div className="flex-1 min-w-0">
        <p className="text-sm">
          <span className="font-medium">{getAgentDisplayName(activity.agent_id)}</span>
          {" "}
          <span className="text-muted-foreground">{label}</span>
          {activity.task_title && (
            <>
              {" "}
              <span className="font-medium">{activity.task_title}</span>
            </>
          )}
        </p>
        <span className="text-xs text-muted-foreground">
          {activity.timestamp ? formatTime(activity.timestamp) : "N/A"}
        </span>
      </div>
    </div>
  );
}
