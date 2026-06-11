"use client";

import { useCeoOverview, useAuditorFlags, useRecentActivity } from "@/hooks/use-dashboard";
import { useTasks } from "@/hooks/use-tasks";
import { useUsageStore } from "@/store/usage-store";
import { TeamHealthCards } from "./team-health-cards";
import { KeyMetricsPanel } from "./key-metrics-panel";
import { AuditorAlertsPanel } from "./auditor-alerts-panel";
import { ActiveBlockersPanel } from "./active-blockers-panel";
import { RecentActivityFeed } from "./recent-activity-feed";
import { QuickActionsBar } from "./quick-actions-bar";
import { CeoApprovalQueue } from "./ceo-approval-queue";
import type { Activity } from "./activity-item";
import { Button } from "@/components/ui/button";
import { UsageOverviewPanel } from "./usage-overview-panel";
import { RefreshCw, Settings } from "lucide-react";
import Link from "next/link";

export function CommandCenter() {
  // WS-first data source: read live usage data from the store (populated by
  // useRateLimitWebSocket whenever USAGE_UPDATE / USAGE_SNAPSHOT arrives on
  // the /ws/system connection). Falls back to the polling hook when WS is
  // not connected or no snapshot has been received yet.
  const { wsState, usageData } = useUsageStore();
  const wsConnected = wsState === "connected" && usageData !== null;

  // Polling hook always runs (background refetch every 60 s) so the data is
  // ready the moment WS disconnects.
  const { data: overview, isLoading: loadingOverview, refetch: refetchOverview } = useCeoOverview();
  const { data: flags, isLoading: loadingFlags } = useAuditorFlags({ resolved: false });
  const { data: tasks, isLoading: loadingTasks } = useTasks();
  const { data: activity, isLoading: loadingActivity } = useRecentActivity(24);

  // key_metrics: prefer WS snapshot; fall back to polling
  const keyMetrics = wsConnected
    ? usageData.key_metrics
    : overview?.key_metrics;
  const isLoadingMetrics = wsConnected ? false : loadingOverview;

  const handleRefresh = () => {
    refetchOverview();
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

      {/* Team Health */}
      <section>
        <h2 className="text-lg font-semibold mb-4">Team Health</h2>
        <TeamHealthCards
          teams={overview?.health_status}
          isLoading={loadingOverview}
        />
      </section>

      {/* CEO Approval Queue - Your primary action item */}
      <section>
        <CeoApprovalQueue />
      </section>

      {/* Metrics, Alerts, and Usage Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <KeyMetricsPanel
          metrics={keyMetrics}
          isLoading={isLoadingMetrics}
          wsState={wsState}
        />
        <AuditorAlertsPanel alerts={flags} isLoading={loadingFlags} />
        <UsageOverviewPanel />
      </div>

      {/* Blockers and Activity Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
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
