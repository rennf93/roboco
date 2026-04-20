"use client";

import { CommitRef } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { GitCommit, Clock, User } from "lucide-react";
import { getAgentDisplayName } from "@/lib/agent-utils";

interface CommitCardProps {
  commit: CommitRef;
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffHours / 24);

  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

export function CommitCard({ commit }: CommitCardProps) {
  return (
    <Card className="overflow-hidden">
      <CardContent className="pt-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3 flex-1 min-w-0">
            <div className="bg-primary/10 rounded-full p-2">
              <GitCommit className="h-4 w-4 text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              {/* Commit message */}
              <p className="font-medium text-sm leading-tight mb-1">
                {commit.message}
              </p>

              {/* Meta info */}
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                {/* Hash */}
                <Badge variant="outline" className="font-mono text-xs">
                  {commit.hash.slice(0, 7)}
                </Badge>

                {/* Author */}
                {commit.author_agent_id && (
                  <span className="flex items-center gap-1">
                    <User className="h-3 w-3" />
                    {getAgentDisplayName(commit.author_agent_id)}
                  </span>
                )}

                {/* Time */}
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {formatTime(commit.timestamp)}
                </span>
              </div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
