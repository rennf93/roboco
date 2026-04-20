"use client";

import { KBIndexType, KBStats } from "@/types";
import { Code, FileText, MessageSquare, BookOpen, ChevronRight, AlertTriangle, Scale, GitBranch, ClipboardCheck, Lightbulb } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

const categoryConfig: Record<KBIndexType, { label: string; description: string; icon: React.ReactNode }> = {
  [KBIndexType.CODE]: {
    label: "Code",
    description: "Source code, functions, and classes",
    icon: <Code className="h-5 w-5 text-purple-500" />,
  },
  [KBIndexType.DOCUMENTATION]: {
    label: "Documentation",
    description: "READMEs, guides, and API docs",
    icon: <FileText className="h-5 w-5 text-blue-500" />,
  },
  [KBIndexType.CONVERSATIONS]: {
    label: "Conversations",
    description: "Agent discussions and decisions",
    icon: <MessageSquare className="h-5 w-5 text-green-500" />,
  },
  [KBIndexType.JOURNALS]: {
    label: "Journals",
    description: "Agent reflections and learnings",
    icon: <BookOpen className="h-5 w-5 text-orange-500" />,
  },
  [KBIndexType.ERRORS]: {
    label: "Errors",
    description: "Error patterns and solutions",
    icon: <AlertTriangle className="h-5 w-5 text-red-500" />,
  },
  [KBIndexType.STANDARDS]: {
    label: "Standards",
    description: "Coding, security, and workflow rules",
    icon: <Scale className="h-5 w-5 text-cyan-500" />,
  },
  [KBIndexType.DECISIONS]: {
    label: "Decisions",
    description: "Architectural and design decisions",
    icon: <GitBranch className="h-5 w-5 text-indigo-500" />,
  },
  [KBIndexType.REVIEWS]: {
    label: "Reviews",
    description: "Code review feedback",
    icon: <ClipboardCheck className="h-5 w-5 text-pink-500" />,
  },
  [KBIndexType.LEARNINGS]: {
    label: "Learnings",
    description: "Cross-agent shared learnings",
    icon: <Lightbulb className="h-5 w-5 text-yellow-500" />,
  },
};

interface KBCategoryNavProps {
  stats: KBStats | undefined;
  isLoading: boolean;
  selectedCategory: KBIndexType | null;
  onSelectCategory: (category: KBIndexType) => void;
}

export function KBCategoryNav({
  stats,
  isLoading,
  selectedCategory,
  onSelectCategory,
}: KBCategoryNavProps) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="flex items-center gap-3 p-3 rounded-lg border">
            <Skeleton className="h-10 w-10 rounded" />
            <div className="flex-1 space-y-1">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-3 w-40" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  const getDocCount = (type: KBIndexType) => {
    if (!stats || !Array.isArray(stats.indexes)) return 0;
    const idx = stats.indexes.find((i) => i.index_type === type);
    return idx?.document_count ?? 0;
  };

  return (
    <div className="space-y-2">
      {Object.values(KBIndexType).map((type) => {
        const config = categoryConfig[type];
        const count = getDocCount(type);
        const isSelected = selectedCategory === type;

        return (
          <button
            key={type}
            onClick={() => onSelectCategory(type)}
            className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors text-left ${
              isSelected
                ? "bg-primary/10 border-primary"
                : "hover:bg-muted/50 border-transparent hover:border-border"
            }`}
          >
            <div className="shrink-0">{config.icon}</div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between">
                <span className="font-medium text-sm">{config.label}</span>
                <span className="text-xs text-muted-foreground">
                  {count.toLocaleString()} docs
                </span>
              </div>
              <p className="text-xs text-muted-foreground truncate">{config.description}</p>
            </div>
            <ChevronRight className={`h-4 w-4 text-muted-foreground shrink-0 transition-transform ${isSelected ? "rotate-90" : ""}`} />
          </button>
        );
      })}
    </div>
  );
}
