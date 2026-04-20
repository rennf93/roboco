"use client";

import { ProgressUpdate } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { MessageSquare, Clock } from "lucide-react";
import { getAgentDisplayName } from "@/lib/agent-utils";

interface ProgressTimelineProps {
  updates: ProgressUpdate[];
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ProgressTimeline({ updates }: ProgressTimelineProps) {
  // Sort by most recent first
  const sortedUpdates = [...updates].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  // Get latest percentage if available
  const latestWithPercentage = sortedUpdates.find((u) => u.percentage !== null);
  const currentProgress = latestWithPercentage?.percentage ?? 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">Progress Updates</CardTitle>
          <span className="text-sm text-muted-foreground">
            {updates.length} update{updates.length !== 1 ? "s" : ""}
          </span>
        </div>
        {latestWithPercentage && (
          <div className="mt-2">
            <div className="flex items-center justify-between text-sm mb-1">
              <span className="text-muted-foreground">Overall Progress</span>
              <span className="font-medium">{currentProgress}%</span>
            </div>
            <Progress value={currentProgress} className="h-2" />
          </div>
        )}
      </CardHeader>
      <CardContent>
        {sortedUpdates.length === 0 ? (
          <p className="text-muted-foreground italic">No progress updates yet.</p>
        ) : (
          <div className="relative">
            {/* Timeline line */}
            <div className="absolute left-3 top-0 bottom-0 w-0.5 bg-border" />

            <ul className="space-y-4">
              {sortedUpdates.map((update, idx) => (
                <li key={idx} className="relative pl-8">
                  {/* Timeline dot */}
                  <div className="absolute left-0 top-1.5 w-6 h-6 rounded-full bg-background border-2 border-primary flex items-center justify-center">
                    <MessageSquare className="h-3 w-3 text-primary" />
                  </div>

                  <div className="bg-muted/50 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium">
                        {getAgentDisplayName(update.agent_id)}
                      </span>
                      <span className="text-xs text-muted-foreground flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {formatTime(update.timestamp)}
                      </span>
                    </div>
                    <p className="text-sm">{update.message}</p>
                    {update.percentage !== null && (
                      <div className="mt-2">
                        <div className="flex items-center gap-2">
                          <Progress value={update.percentage} className="h-1.5 flex-1" />
                          <span className="text-xs text-muted-foreground w-10 text-right">
                            {update.percentage}%
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
