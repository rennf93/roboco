"use client";

import { Journal, GrowthMetrics } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import {
  BookOpen,
  Lightbulb,
  AlertTriangle,
  GitBranch,
  TrendingUp,
} from "lucide-react";

interface GrowthSummaryProps {
  journal: Journal | undefined;
  growth: GrowthMetrics | undefined;
  isLoading: boolean;
}

export function GrowthSummary({
  journal,
  growth,
  isLoading,
}: GrowthSummaryProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className="h-6 w-40" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </CardContent>
      </Card>
    );
  }

  if (!journal) {
    return null;
  }

  const entries = journal.entries_by_type || {};

  return (
    <Card>
      <CardHeader className="pb-3">
        <HelpTip label="Aggregated counts and trends from this agent's journal entries">
          <CardTitle className="text-lg flex items-center gap-2">
            <TrendingUp className="h-5 w-5" />
            Growth Summary
          </CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Total Entries */}
        <HelpTip label="Total journal entries this agent has written across all types">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Total Entries</span>
            <span className="font-medium">{journal.total_entries}</span>
          </div>
        </HelpTip>

        {/* Entry Types Breakdown */}
        <div className="space-y-3">
          <HelpTip label="Wrap-up reflections the agent wrote after finishing tasks">
            <div className="flex items-center justify-between text-sm">
              <div className="flex items-center gap-2 text-muted-foreground">
                <BookOpen className="h-4 w-4 text-blue-500" />
                Task Reflections
              </div>
              <span className="font-medium">
                {entries["task_reflection"] || growth?.total_reflections || 0}
              </span>
            </div>
          </HelpTip>

          <HelpTip label="Records of decisions the agent made, with the reasoning behind them">
            <div className="flex items-center justify-between text-sm">
              <div className="flex items-center gap-2 text-muted-foreground">
                <GitBranch className="h-4 w-4 text-purple-500" />
                Decision Logs
              </div>
              <span className="font-medium">
                {entries["decision_log"] || growth?.total_decisions || 0}
              </span>
            </div>
          </HelpTip>

          <HelpTip label="Lessons learned; broadcast to other agents as a knowledge-share notification">
            <div className="flex items-center justify-between text-sm">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Lightbulb className="h-4 w-4 text-green-500" />
                Learnings
              </div>
              <span className="font-medium">
                {entries["learning"] || growth?.total_learnings || 0}
              </span>
            </div>
          </HelpTip>

          <HelpTip label="Difficulties the agent hit; some are later marked resolved">
            <div className="flex items-center justify-between text-sm">
              <div className="flex items-center gap-2 text-muted-foreground">
                <AlertTriangle className="h-4 w-4 text-orange-500" />
                Struggles
              </div>
              <span className="font-medium">
                {entries["struggle"] || growth?.total_struggles || 0}
              </span>
            </div>
          </HelpTip>
        </div>

        {/* Struggle Resolution Rate */}
        {growth && growth.struggle_resolution_rate > 0 && (
          <HelpTip label="Share of logged struggles that a later entry marked resolved">
            <div className="pt-3 border-t">
              <div className="flex items-center justify-between text-sm mb-2">
                <span className="text-muted-foreground">Struggle Resolution</span>
                <span className="font-medium">
                  {Math.round(growth.struggle_resolution_rate * 100)}%
                </span>
              </div>
              <Progress
                value={growth.struggle_resolution_rate * 100}
                className="h-2"
              />
            </div>
          </HelpTip>
        )}

        {/* Sentiment Trend */}
        {growth?.sentiment_trend && (
          <HelpTip label="Direction of the agent's self-reported sentiment across recent entries">
            <div className="pt-3 border-t">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">Sentiment Trend</span>
                <span className="font-medium capitalize">
                  {growth.sentiment_trend}
                </span>
              </div>
            </div>
          </HelpTip>
        )}
      </CardContent>
    </Card>
  );
}
