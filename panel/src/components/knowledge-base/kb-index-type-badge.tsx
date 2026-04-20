"use client";

import { Badge } from "@/components/ui/badge";
import { KBIndexType } from "@/types";
import { Code, FileText, MessageSquare, BookOpen, AlertTriangle, Scale, GitBranch, ClipboardCheck, Lightbulb } from "lucide-react";

const indexTypeConfig: Record<KBIndexType, { label: string; color: string; icon: React.ReactNode }> = {
  [KBIndexType.CODE]: {
    label: "Code",
    color: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
    icon: <Code className="h-3 w-3" />,
  },
  [KBIndexType.DOCUMENTATION]: {
    label: "Docs",
    color: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    icon: <FileText className="h-3 w-3" />,
  },
  [KBIndexType.CONVERSATIONS]: {
    label: "Conversations",
    color: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
    icon: <MessageSquare className="h-3 w-3" />,
  },
  [KBIndexType.JOURNALS]: {
    label: "Journals",
    color: "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
    icon: <BookOpen className="h-3 w-3" />,
  },
  [KBIndexType.ERRORS]: {
    label: "Errors",
    color: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
    icon: <AlertTriangle className="h-3 w-3" />,
  },
  [KBIndexType.STANDARDS]: {
    label: "Standards",
    color: "bg-cyan-100 text-cyan-700 dark:bg-cyan-900 dark:text-cyan-300",
    icon: <Scale className="h-3 w-3" />,
  },
  [KBIndexType.DECISIONS]: {
    label: "Decisions",
    color: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300",
    icon: <GitBranch className="h-3 w-3" />,
  },
  [KBIndexType.REVIEWS]: {
    label: "Reviews",
    color: "bg-pink-100 text-pink-700 dark:bg-pink-900 dark:text-pink-300",
    icon: <ClipboardCheck className="h-3 w-3" />,
  },
  [KBIndexType.LEARNINGS]: {
    label: "Learnings",
    color: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
    icon: <Lightbulb className="h-3 w-3" />,
  },
};

interface KBIndexTypeBadgeProps {
  indexType: KBIndexType;
  showIcon?: boolean;
  className?: string;
}

export function KBIndexTypeBadge({ indexType, showIcon = true, className }: KBIndexTypeBadgeProps) {
  const config = indexTypeConfig[indexType];

  return (
    <Badge variant="secondary" className={`${config.color} ${className ?? ""}`}>
      {showIcon && <span className="mr-1">{config.icon}</span>}
      {config.label}
    </Badge>
  );
}

export function getIndexTypeIcon(indexType: KBIndexType) {
  return indexTypeConfig[indexType].icon;
}

export function getIndexTypeLabel(indexType: KBIndexType) {
  return indexTypeConfig[indexType].label;
}
