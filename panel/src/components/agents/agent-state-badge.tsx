import { Badge } from "@/components/ui/badge";
import { Clock, RefreshCw, Activity, AlertTriangle, Square } from "lucide-react";

// Agent states as returned by backend orchestrator
type AgentStateString =
  | "idle"
  | "starting"
  | "ready"
  | "running"
  | "waiting_long"
  | "error"
  | "stopped"
  | "terminated";

const stateColors: Record<string, string> = {
  idle: "bg-gray-500",
  starting: "bg-yellow-500",
  ready: "bg-blue-500",
  running: "bg-green-500",
  waiting_long: "bg-orange-500",
  error: "bg-red-500",
  stopped: "bg-gray-400",
  terminated: "bg-gray-600",
};

const stateIcons: Record<string, React.ReactNode> = {
  idle: <Clock className="h-4 w-4" />,
  starting: <RefreshCw className="h-4 w-4 animate-spin" />,
  ready: <Activity className="h-4 w-4" />,
  running: <Activity className="h-4 w-4" />,
  waiting_long: <AlertTriangle className="h-4 w-4" />,
  error: <AlertTriangle className="h-4 w-4" />,
  stopped: <Square className="h-4 w-4" />,
  terminated: <Square className="h-4 w-4" />,
};

interface AgentStateBadgeProps {
  state: AgentStateString | string;
  showIcon?: boolean;
  size?: "sm" | "md" | "lg";
}

export function AgentStateBadge({ state, showIcon = true, size = "md" }: AgentStateBadgeProps) {
  const sizeClasses = {
    sm: "text-xs px-2 py-0.5",
    md: "text-sm px-2.5 py-0.5",
    lg: "text-lg px-3 py-1",
  };

  const color = stateColors[state] || "bg-gray-400";
  const icon = stateIcons[state] || <Square className="h-4 w-4" />;

  return (
    <Badge className={`${color} text-white ${sizeClasses[size]}`}>
      {showIcon && <span className="mr-1">{icon}</span>}
      {state.replace(/_/g, " ")}
    </Badge>
  );
}

export { stateColors, stateIcons };
