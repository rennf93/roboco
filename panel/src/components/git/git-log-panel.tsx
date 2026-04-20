"use client";

import { GitLogResponse, CommitInfo } from "@/types/git";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { GitCommit, User, Calendar } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

interface GitLogPanelProps {
  log: GitLogResponse | undefined;
  isLoading: boolean;
  onSelectCommit?: (commit: CommitInfo) => void;
  selectedHash?: string;
}

export function GitLogPanel({
  log,
  isLoading,
  onSelectCommit,
  selectedHash,
}: GitLogPanelProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex gap-3">
              <Skeleton className="h-8 w-8 rounded-full shrink-0" />
              <div className="flex-1 space-y-1">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-3 w-24" />
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    );
  }

  if (!log || log.commits.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-muted-foreground">
          <GitCommit className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p>No commits found</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <GitCommit className="h-4 w-4" />
          Commit History
          <Badge variant="secondary" className="ml-auto text-xs">
            {log.branch}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <ScrollArea className="h-80">
          <div className="p-4 space-y-0">
            {log.commits.map((commit, index) => (
              <button
                key={commit.hash}
                onClick={() => onSelectCommit?.(commit)}
                className={
                  "w-full text-left p-3 rounded-lg transition-colors relative " +
                  (selectedHash === commit.hash
                    ? "bg-primary/10"
                    : "hover:bg-muted")
                }
              >
                {/* Timeline line */}
                {index < log.commits.length - 1 && (
                  <div className="absolute left-6 top-10 bottom-0 w-0.5 bg-border" />
                )}

                <div className="flex gap-3">
                  {/* Commit dot */}
                  <div className="relative z-10">
                    <div
                      className={
                        "h-4 w-4 rounded-full border-2 mt-0.5 " +
                        (index === 0
                          ? "bg-primary border-primary"
                          : "bg-background border-muted-foreground")
                      }
                    />
                  </div>

                  {/* Commit info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium leading-snug line-clamp-2">
                        {commit.message}
                      </p>
                      <Badge
                        variant="outline"
                        className="font-mono text-xs shrink-0"
                      >
                        {commit.short_hash}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <User className="h-3 w-3" />
                        {commit.author}
                      </span>
                      <span className="flex items-center gap-1">
                        <Calendar className="h-3 w-3" />
                        {formatDistanceToNow(new Date(commit.date), {
                          addSuffix: true,
                        })}
                      </span>
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
