"use client";

import { AuditorFlag, FlagSeverity } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Shield, ArrowRight } from "lucide-react";
import Link from "next/link";

interface AuditorAlertsPanelProps {
  alerts: AuditorFlag[] | undefined;
  isLoading: boolean;
}

const severityColors: Record<FlagSeverity, string> = {
  [FlagSeverity.INFO]: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  [FlagSeverity.WARNING]: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200",
  [FlagSeverity.URGENT]: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200",
};

const severityEmoji: Record<FlagSeverity, string> = {
  [FlagSeverity.INFO]: "\uD83D\uDFE2",
  [FlagSeverity.WARNING]: "\uD83D\uDFE1",
  [FlagSeverity.URGENT]: "\uD83D\uDD34",
};

export function AuditorAlertsPanel({ alerts, isLoading }: AuditorAlertsPanelProps) {
  // Filter to show only unresolved, sorted by severity
  const unresolvedAlerts = (alerts ?? [])
    .filter((a) => !a.resolved_at)
    .sort((a, b) => {
      const order: Record<FlagSeverity, number> = {
        [FlagSeverity.URGENT]: 0,
        [FlagSeverity.WARNING]: 1,
        [FlagSeverity.INFO]: 2,
      };
      return order[a.severity] - order[b.severity];
    })
    .slice(0, 5);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <Shield className="h-5 w-5" />
            Auditor Alerts
          </CardTitle>
          {unresolvedAlerts.length > 0 && (
            <Badge variant="destructive">{unresolvedAlerts.length}</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : unresolvedAlerts.length === 0 ? (
          <div className="text-center py-4 text-muted-foreground text-sm">
            <Shield className="h-8 w-8 mx-auto mb-2 opacity-50" />
            No active alerts
          </div>
        ) : (
          <div className="space-y-3">
            {unresolvedAlerts.map((alert) => (
              <div
                key={alert.id}
                className="flex items-start gap-3 p-3 rounded-lg border bg-muted/30"
              >
                <span className="text-lg">{severityEmoji[alert.severity]}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-sm truncate">{alert.title}</span>
                    <Badge className={severityColors[alert.severity] + " text-xs"}>
                      {alert.severity}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground line-clamp-1">
                    {alert.description}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="mt-4 pt-3 border-t">
          <Link href="/auditor">
            <Button variant="ghost" size="sm" className="w-full">
              View All Flags
              <ArrowRight className="h-4 w-4 ml-2" />
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
