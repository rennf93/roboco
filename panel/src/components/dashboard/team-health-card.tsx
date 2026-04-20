"use client";

import { TeamHealth } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { HealthIndicator } from "./health-indicator";
import { Users, AlertTriangle, TrendingUp } from "lucide-react";

interface TeamHealthCardProps {
  health: TeamHealth;
}

export function TeamHealthCard({ health }: TeamHealthCardProps) {
  const teamName = health.team.replace(/_/g, " ");

  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg capitalize">{teamName}</CardTitle>
          <HealthIndicator status={health.status} size="sm" />
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Active Tasks */}
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Users className="h-4 w-4" />
            Active
          </div>
          <span className="font-medium">{health.active_tasks}</span>
        </div>

        {/* Blocked Tasks */}
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <AlertTriangle className="h-4 w-4" />
            Blocked
          </div>
          <span className={`font-medium ${health.blocked_tasks > 0 ? "text-red-600" : ""}`}>
            {health.blocked_tasks}
          </span>
        </div>

        {/* Completed This Week */}
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <TrendingUp className="h-4 w-4" />
            Completed (7d)
          </div>
          <span className="font-medium">{health.completed_this_week}</span>
        </div>

        {/* Blocked Ratio */}
        {health.blocked_ratio > 0 && (
          <div className="pt-2 border-t">
            <Badge
              variant={health.blocked_ratio > 0.3 ? "destructive" : health.blocked_ratio > 0.1 ? "secondary" : "outline"}
            >
              {Math.round(health.blocked_ratio * 100)}% blocked
            </Badge>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
