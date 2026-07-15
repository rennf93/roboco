import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";
import {
  Clock,
  RefreshCw,
  Activity,
  AlertTriangle,
  Square,
  PowerOff,
} from "lucide-react";

// Agent states as returned by backend orchestrator
type AgentStateString =
  | "active"
  | "idle"
  | "starting"
  | "ready"
  | "running"
  | "waiting_long"
  | "paused"
  | "error"
  | "offline"
  | "stopped"
  | "terminated";

// Clear, distinct indicators:
//   active / running → green; offline → grey; stopped / paused → amber
//   (needs attention, not alarming); error → red.
const stateColors: Record<string, string> = {
  active: "bg-green-500",
  running: "bg-green-500",
  ready: "bg-blue-500",
  starting: "bg-yellow-500",
  idle: "bg-blue-400",
  stopped: "bg-amber-500",
  paused: "bg-amber-500",
  waiting_long: "bg-orange-500",
  offline: "bg-gray-500",
  terminated: "bg-gray-600",
  error: "bg-red-500",
};

const stateIcons: Record<string, React.ReactNode> = {
  active: <Activity className="h-4 w-4" />,
  idle: <Clock className="h-4 w-4" />,
  starting: <RefreshCw className="h-4 w-4 animate-spin" />,
  ready: <Activity className="h-4 w-4" />,
  running: <Activity className="h-4 w-4" />,
  waiting_long: <AlertTriangle className="h-4 w-4" />,
  paused: <Square className="h-4 w-4" />,
  error: <AlertTriangle className="h-4 w-4" />,
  offline: <PowerOff className="h-4 w-4" />,
  stopped: <Square className="h-4 w-4" />,
  terminated: <Square className="h-4 w-4" />,
};

const stateDescriptions: Record<string, string> = {
  active: "Agent is actively working a task.",
  running: "Agent is running — currently executing.",
  ready: "Agent is ready and waiting for work.",
  starting: "Agent container is starting up.",
  idle: "Agent is idle — no task currently claimed.",
  waiting_long: "Agent has been waiting a long time; may need attention.",
  paused: "Agent is paused; can resume.",
  stopped: "Agent container is stopped; can be restarted.",
  terminated: "Agent container has been terminated.",
  offline: "Agent is offline.",
  error: "Agent hit an error state; needs attention.",
};

interface AgentStateBadgeProps {
  state: AgentStateString | string;
  showIcon?: boolean;
  size?: "sm" | "md" | "lg";
}

/** Plain-language explanation for an agent state. Reused by the shared badge
 * and by inline agent-state renderers. Empty for an unknown state. */
export function agentStateDescription(state: string): string {
  return stateDescriptions[state] ?? "";
}

export function AgentStateBadge({
  state,
  showIcon = true,
  size = "md",
}: AgentStateBadgeProps) {
  const sizeClasses = {
    sm: "text-xs px-2 py-0.5",
    md: "text-sm px-2.5 py-0.5",
    lg: "text-lg px-3 py-1",
  };

  const color = stateColors[state] || "bg-gray-400";
  const icon = stateIcons[state] || <Square className="h-4 w-4" />;

  return (
    <HelpTip label={stateDescriptions[state]}>
      <Badge className={`${color} text-white ${sizeClasses[size]}`}>
        {showIcon && <span className="mr-1">{icon}</span>}
        {state.replace(/_/g, " ")}
      </Badge>
    </HelpTip>
  );
}

export { stateColors, stateIcons };
