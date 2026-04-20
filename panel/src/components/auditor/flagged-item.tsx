"use client";

import { AuditorFlag, FlagSeverity } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Eye, CheckCircle, Send, Clock } from "lucide-react";
import Link from "next/link";

interface FlaggedItemProps {
  flag: AuditorFlag;
  onResolve?: (flagId: string) => void;
  onReportToCeo?: (flag: AuditorFlag) => void;
}

const severityColors: Record<FlagSeverity, string> = {
  [FlagSeverity.INFO]: "bg-blue-100 text-blue-700",
  [FlagSeverity.WARNING]: "bg-yellow-100 text-yellow-700",
  [FlagSeverity.URGENT]: "bg-red-100 text-red-700",
};

const severityEmoji: Record<FlagSeverity, string> = {
  [FlagSeverity.INFO]: "\uD83D\uDFE2",
  [FlagSeverity.WARNING]: "\uD83D\uDFE1",
  [FlagSeverity.URGENT]: "\uD83D\uDD34",
};

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));

  if (diffHours < 1) return "< 1h ago";
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export function FlaggedItem({ flag, onResolve, onReportToCeo }: FlaggedItemProps) {
  const isResolved = !!flag.resolved_at;

  return (
    <div
      className={`p-4 rounded-lg border ${
        isResolved ? "bg-muted/30 opacity-60" : "bg-muted/50"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3 flex-1 min-w-0">
          <span className="text-xl">{severityEmoji[flag.severity]}</span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <span className="font-medium text-sm">{flag.title}</span>
              <Badge className={severityColors[flag.severity] + " text-xs"}>
                {flag.severity}
              </Badge>
              <Badge variant="outline" className="text-xs">
                {flag.category}
              </Badge>
              {isResolved && (
                <Badge className="bg-green-100 text-green-700 text-xs">
                  Resolved
                </Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground mb-2">{flag.description}</p>
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {formatTime(flag.created_at)}
              </span>
              {flag.related_task_id && (
                <Link href={"/tasks/" + flag.related_task_id}>
                  <span className="text-primary hover:underline">
                    Task #{flag.related_task_id.slice(0, 8)}
                  </span>
                </Link>
              )}
            </div>
          </div>
        </div>
        {!isResolved && (
          <div className="flex items-center gap-2 shrink-0">
            {flag.related_task_id && (
              <Link href={"/tasks/" + flag.related_task_id}>
                <Button variant="ghost" size="sm">
                  <Eye className="h-4 w-4" />
                </Button>
              </Link>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => onResolve?.(flag.id)}
            >
              <CheckCircle className="h-4 w-4 mr-1" />
              Resolve
            </Button>
            {flag.severity === FlagSeverity.URGENT && (
              <Button
                variant="default"
                size="sm"
                onClick={() => onReportToCeo?.(flag)}
              >
                <Send className="h-4 w-4 mr-1" />
                Report CEO
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
