"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { KBStats, KBIndexType } from "@/types";
import { Database, Code, FileText, MessageSquare, BookOpen, AlertTriangle, Scale, GitBranch, ClipboardCheck, Lightbulb } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

const indexIcons: Record<KBIndexType, React.ReactNode> = {
  [KBIndexType.CODE]: <Code className="h-4 w-4 text-purple-500" />,
  [KBIndexType.DOCUMENTATION]: <FileText className="h-4 w-4 text-blue-500" />,
  [KBIndexType.CONVERSATIONS]: <MessageSquare className="h-4 w-4 text-green-500" />,
  [KBIndexType.JOURNALS]: <BookOpen className="h-4 w-4 text-orange-500" />,
  [KBIndexType.ERRORS]: <AlertTriangle className="h-4 w-4 text-red-500" />,
  [KBIndexType.STANDARDS]: <Scale className="h-4 w-4 text-cyan-500" />,
  [KBIndexType.DECISIONS]: <GitBranch className="h-4 w-4 text-indigo-500" />,
  [KBIndexType.REVIEWS]: <ClipboardCheck className="h-4 w-4 text-pink-500" />,
  [KBIndexType.LEARNINGS]: <Lightbulb className="h-4 w-4 text-yellow-500" />,
};

const indexLabels: Record<KBIndexType, string> = {
  [KBIndexType.CODE]: "Code",
  [KBIndexType.DOCUMENTATION]: "Docs",
  [KBIndexType.CONVERSATIONS]: "Convos",
  [KBIndexType.JOURNALS]: "Journals",
  [KBIndexType.ERRORS]: "Errors",
  [KBIndexType.STANDARDS]: "Standards",
  [KBIndexType.DECISIONS]: "Decisions",
  [KBIndexType.REVIEWS]: "Reviews",
  [KBIndexType.LEARNINGS]: "Learnings",
};

interface KBStatsCardProps {
  stats: KBStats | undefined;
  isLoading: boolean;
}

export function KBStatsCard({ stats, isLoading }: KBStatsCardProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Database className="h-4 w-4" />
            Index Stats
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="flex items-center justify-between">
              <Skeleton className="h-4 w-16" />
              <Skeleton className="h-4 w-10" />
            </div>
          ))}
        </CardContent>
      </Card>
    );
  }

  if (!stats || !Array.isArray(stats.indexes)) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Database className="h-4 w-4" />
            Index Stats
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No data available</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <Database className="h-4 w-4" />
          Index Stats
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {stats.indexes.map((idx) => (
          <div key={idx.index_type} className="flex items-center justify-between text-sm">
            <div className="flex items-center gap-2">
              {indexIcons[idx.index_type]}
              <span>{indexLabels[idx.index_type]}</span>
            </div>
            <div className="text-right">
              <span className="font-medium">{idx.document_count.toLocaleString()}</span>
              <span className="text-muted-foreground text-xs ml-1">docs</span>
            </div>
          </div>
        ))}
        <div className="pt-2 border-t">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">Total</span>
            <span className="font-bold">{stats.total_documents.toLocaleString()}</span>
          </div>
          <div className="flex items-center justify-between text-xs text-muted-foreground mt-1">
            <span>Chunks</span>
            <span>{stats.total_chunks.toLocaleString()}</span>
          </div>
        </div>
        {stats.indexes[0]?.last_updated && (
          <p className="text-xs text-muted-foreground pt-1">
            Updated {formatDistanceToNow(new Date(stats.indexes[0].last_updated))} ago
          </p>
        )}
      </CardContent>
    </Card>
  );
}
