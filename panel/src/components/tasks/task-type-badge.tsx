import { TaskType } from "@/types";
import { Badge } from "@/components/ui/badge";
import {
  Code,
  FileText,
  Search,
  GitBranch,
  Palette,
  ClipboardList,
} from "lucide-react";

interface TaskTypeBadgeProps {
  type?: TaskType;
  showLabel?: boolean;
  className?: string;
}

const TYPE_CONFIG: Record<TaskType, { label: string; icon: React.ReactNode; color: string }> = {
  [TaskType.CODE]: {
    label: "Code",
    icon: <Code className="h-3 w-3" />,
    color: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
  },
  [TaskType.DOCUMENTATION]: {
    label: "Docs",
    icon: <FileText className="h-3 w-3" />,
    color: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
  },
  [TaskType.RESEARCH]: {
    label: "Research",
    icon: <Search className="h-3 w-3" />,
    color: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
  },
  [TaskType.PLANNING]: {
    label: "Planning",
    icon: <GitBranch className="h-3 w-3" />,
    color: "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
  },
  [TaskType.DESIGN]: {
    label: "Design",
    icon: <Palette className="h-3 w-3" />,
    color: "bg-pink-100 text-pink-700 dark:bg-pink-900 dark:text-pink-300",
  },
  [TaskType.ADMINISTRATIVE]: {
    label: "Admin",
    icon: <ClipboardList className="h-3 w-3" />,
    color: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  },
};

export function TaskTypeBadge({ type, showLabel = true, className = "" }: TaskTypeBadgeProps) {
  if (!type) return null;

  const config = TYPE_CONFIG[type];
  if (!config) return null;

  return (
    <Badge variant="outline" className={`${config.color} ${className}`}>
      <span className="flex items-center gap-1">
        {config.icon}
        {showLabel && <span>{config.label}</span>}
      </span>
    </Badge>
  );
}
