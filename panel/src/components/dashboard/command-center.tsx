"use client";

import { useCeoOverview, useAuditorFlags, useRecentActivity } from "@/hooks/use-dashboard";
import { useTasks } from "@/hooks/use-tasks";
import { TeamHealthCards } from "./team-health-cards";
import { KeyMetricsPanel } from "./key-metrics-panel";
import { AuditorAlertsPanel } from "./auditor-alerts-panel";
import { ActiveBlockersPanel } from "./active-blockers-panel";
import { RecentActivityFeed } from "./recent-activity-feed";
import { QuickActionsBar } from "./quick-actions-bar";
import { CeoApprovalQueue } from "./ceo-approval-queue";
import { StrategySignalsPanel } from "./strategy-signals-panel";
import type { Activity } from "./activity-item";
import { Button } from "@/components/ui/button";
import { UsageOverviewPanel } from "./usage-overview-panel";
import { RefreshCw, Settings, AlertCircle } from "lucide-react";
import Link from "next/link";

export function CommandCenter() {
  const { data: overview, isLoading: loadingOverview, isError: errorOverview, refetch: refetchOverview } = useCeoOverview();
  const { data: flags, isLoading: loadingFlags, isError: errorFlags, refetch: refetchFlags } = useAuditorFlags({ resolved: false });
  const { data: tasks, isLoading: loadingTasks, isError: errorTasks, refetch: refetchTasks } = useTasks();
  const { data: activity, isLoading: loadingActivity, isError: errorActivity, refetch: refetchActivity } = useRecentActivity(24);

  const hasError = errorOverview || errorFlags || errorTasks || errorActivity;

  const handleRefresh = () => {
    refetchOverview();
    refetchFlags();
    refetchTasks();
    refetchActivity();
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">RoboCo Command Center</h1>
          <p className="text-muted-foreground">
            Complete visibility into all operations
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleRefresh}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Link href="/settings">
            <Button variant="ghost" size="icon">
              <Settings className="h-5 w-5" />
            </Button>
          </Link>
        </div>
      </div>

      {/* Error indicator */}
      {hasError && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Some data failed to load. Click Refresh to try again.
        </div>
      )}

      {/* Team Health */}
      <section>
        <h2 className="text-lg font-semibold mb-4">Team Health</h2>
        <TeamHealthCards
          teams={overview?.health_status}
          isLoading={loadingOverview}
        />
      </section>

      {/* CEO Approval Queue + Strategy Signals - side-by-side on lg+ */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <CeoApprovalQueue />
        <StrategySignalsPanel />
      </div>

      {/* Metrics, Alerts, and Usage Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 xl:grid-cols-3 2xl:grid-cols-3 gap-6">
        <KeyMetricsPanel
          metrics={overview?.key_metrics}
          isLoading={loadingOverview}
        />
        <AuditorAlertsPanel alerts={flags} isLoading={loadingFlags} />
        <UsageOverviewPanel />
      </div>

      {/* Blockers and Activity Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-2 2xl:grid-cols-2 gap-6">
        <ActiveBlockersPanel tasks={tasks} isLoading={loadingTasks} />
        <RecentActivityFeed
          activities={activity as Activity[] | undefined}
          isLoading={loadingActivity}
        />
      </div>

      {/* Quick Actions */}
      <section className="pt-4 border-t">
        <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
        <QuickActionsBar />
      </section>
    </div>
  );
}
