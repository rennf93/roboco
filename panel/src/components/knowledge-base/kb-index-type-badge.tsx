"use client";

import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";
import { KBIndexType } from "@/types";
import {
  FileText,
  MessageSquare,
  BookOpen,
  AlertTriangle,
  Scale,
  GitBranch,
  ClipboardCheck,
  Lightbulb,
  ScrollText,
  StickyNote,
} from "lucide-react";

const indexTypeConfig: Record<
  KBIndexType,
  {
    label: string;
    color: string;
    icon: React.ReactNode;
    description: string;
  }
> = {
  [KBIndexType.DOCUMENTATION]: {
    label: "Docs",
    color: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    icon: <FileText className="h-3 w-3" />,
    description: "READMEs, guides, and API docs indexed from the repo",
  },
  [KBIndexType.CONVERSATIONS]: {
    label: "Conversations",
    color: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
    icon: <MessageSquare className="h-3 w-3" />,
    description: "Agent-to-agent discussion excerpts and decisions",
  },
  [KBIndexType.JOURNALS]: {
    label: "Journals",
    color:
      "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
    icon: <BookOpen className="h-3 w-3" />,
    description: "Agent reflections, learnings, and personal logs",
  },
  [KBIndexType.ERRORS]: {
    label: "Errors",
    color: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
    icon: <AlertTriangle className="h-3 w-3" />,
    description: "Known error patterns and their solutions",
  },
  [KBIndexType.STANDARDS]: {
    label: "Standards",
    color: "bg-cyan-100 text-cyan-700 dark:bg-cyan-900 dark:text-cyan-300",
    icon: <Scale className="h-3 w-3" />,
    description: "Coding, security, and workflow rules the fleet follows",
  },
  [KBIndexType.DECISIONS]: {
    label: "Decisions",
    color:
      "bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300",
    icon: <GitBranch className="h-3 w-3" />,
    description: "Architectural and design decisions made by agents",
  },
  [KBIndexType.REVIEWS]: {
    label: "Reviews",
    color: "bg-pink-100 text-pink-700 dark:bg-pink-900 dark:text-pink-300",
    icon: <ClipboardCheck className="h-3 w-3" />,
    description: "Code review feedback from QA and PR reviewers",
  },
  [KBIndexType.LEARNINGS]: {
    label: "Learnings",
    color:
      "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
    icon: <Lightbulb className="h-3 w-3" />,
    description: "Cross-agent learnings broadcast as shared knowledge",
  },
  [KBIndexType.PLAYBOOKS]: {
    label: "Playbooks",
    color:
      "bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300",
    icon: <ScrollText className="h-3 w-3" />,
    description: "Curated, Auditor-approved reusable procedures",
  },
  [KBIndexType.VAULT_NOTES]: {
    label: "Vault Notes",
    color:
      "bg-violet-100 text-violet-700 dark:bg-violet-900 dark:text-violet-300",
    icon: <StickyNote className="h-3 w-3" />,
    description: "Human-authored notes from the CEO's Obsidian vault",
  },
};

interface KBIndexTypeBadgeProps {
  indexType: KBIndexType;
  showIcon?: boolean;
  className?: string;
}

export function KBIndexTypeBadge({
  indexType,
  showIcon = true,
  className,
}: KBIndexTypeBadgeProps) {
  const config = indexTypeConfig[indexType];

  return (
    <HelpTip label={config.description}>
      <Badge
        variant="secondary"
        className={`${config.color} ${className ?? ""}`}
      >
        {showIcon && <span className="mr-1">{config.icon}</span>}
        {config.label}
      </Badge>
    </HelpTip>
  );
}

export function getIndexTypeIcon(indexType: KBIndexType) {
  return indexTypeConfig[indexType].icon;
}

export function getIndexTypeLabel(indexType: KBIndexType) {
  return indexTypeConfig[indexType].label;
}

export function getIndexTypeDescription(indexType: KBIndexType) {
  return indexTypeConfig[indexType].description;
}
