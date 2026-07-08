"use client";

import { useEffect } from "react";
import {
  useCeoOverview,
  useAuditorFlags,
  useRecentActivity,
} from "@/hooks/use-dashboard";
import { useTasks } from "@/hooks/use-tasks";
import { usePageRefresh } from "@/hooks/use-page-refresh";
import { TeamHealthCards } from "./team-health-cards";
import { KeyMetricsPanel } from "./key-metrics-panel";
import { AuditorAlertsPanel } from "./auditor-alerts-panel";
import { ActiveBlockersPanel } from "./active-blockers-panel";
import { RecentActivityFeed } from "./recent-activity-feed";
import { QuickActionsBar } from "./quick-actions-bar";
import { CeoApprovalQueue } from "./ceo-approval-queue";
import { PrReviewQueue } from "./pr-review-queue";
import { ReleaseProposalCard } from "./release-proposal-card";
import { PlaybookReviewQueue } from "./playbook-review-queue";
import { XPostQueue } from "./x-post-queue";
import { VideoPostQueue } from "./video-post-queue";
import { RoadmapReviewQueue } from "./roadmap-review-queue";
import { StrategySignalsPanel } from "./strategy-signals-panel";
import type { Activity } from "./activity-item";
import { Button } from "@/components/ui/button";
import { UsageOverviewPanel } from "./usage-overview-panel";
import { ScorecardOverviewPanel } from "./scorecard-overview-panel";
import { Settings, AlertCircle } from "lucide-react";
import Link from "next/link";

export function CommandCenter() {
  const {
    data: overview,
    isLoading: loadingOverview,
    isError: errorOverview,
    refetch: refetchOverview,
  } = useCeoOverview();
  const {
    data: flags,
    isLoading: loadingFlags,
    isError: errorFlags,
    refetch: refetchFlags,
  } = useAuditorFlags({ resolved: false });
  const {
    data: tasks,
    isLoading: loadingTasks,
    isError: errorTasks,
    refetch: refetchTasks,
  } = useTasks();
  const {
    data: activity,
    isLoading: loadingActivity,
    isError: errorActivity,
    refetch: refetchActivity,
  } = useRecentActivity(24);

  const hasError = errorOverview || errorFlags || errorTasks || errorActivity;

  const { register, unregister, setActiveScope } = usePageRefresh();

  useEffect(() => {
    const handleRefresh = () => {
      refetchOverview();
      refetchFlags();
      refetchTasks();
      refetchActivity();
    };

    register("dashboard", handleRefresh);
    setActiveScope("dashboard");
    return () => {
      unregister("dashboard");
      setActiveScope(null);
    };
  }, [
    register,
    unregister,
    setActiveScope,
    refetchOverview,
    refetchFlags,
    refetchTasks,
    refetchActivity,
  ]);

  return (
    // flex-col + explicit `order` (reset via md:order-none): below md the CEO
    // decision queues and activity move above the fold; at md+ every item
    // shares order:0 and falls back to plain source order (unchanged desktop
    // layout).
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="order-1 flex items-center justify-between md:order-none">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            RoboCo Command Center
          </h1>
          <p className="text-muted-foreground">
            Complete visibility into all operations
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/settings" prefetch={false}>
            <Button variant="ghost" size="icon">
              <Settings className="h-5 w-5" />
            </Button>
          </Link>
        </div>
      </div>

      {/* Error indicator */}
      {hasError && (
        <div className="order-2 flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 px-4 py-2 text-sm text-destructive md:order-none">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Some data failed to load. Use the navbar refresh button to try again.
        </div>
      )}

      {/* CEO Approval Queue + Strategy Signals - side-by-side on lg+. Ordered
          first on mobile — the CEO's decisions shouldn't be below the fold. */}
      <div className="order-3 grid grid-cols-1 gap-6 md:order-none lg:grid-cols-2">
        <CeoApprovalQueue />
        <StrategySignalsPanel />
      </div>

      {/* External-PR review decision queue (hidden when empty) */}
      <div className="order-4 md:order-none">
        <PrReviewQueue />
      </div>

      {/* Gated release proposal (hidden when none open) */}
      <div className="order-4 md:order-none">
        <ReleaseProposalCard />
      </div>

      {/* Playbook review queue (hidden when no drafts) */}
      <div className="order-4 md:order-none">
        <PlaybookReviewQueue />
      </div>

      {/* X post/reply queue (hidden when no drafts) */}
      <div className="order-4 md:order-none">
        <XPostQueue />
      </div>

      {/* Video post queue (always visible — carries the on-demand request action) */}
      <div className="order-4 md:order-none">
        <VideoPostQueue />
      </div>

      {/* Board roadmap queue (hidden when no cycle authored) */}
      <div className="order-4 md:order-none">
        <RoadmapReviewQueue />
      </div>

      {/* Blockers and Activity Row — activity brought up near the top on
          mobile too, ahead of the Team Health / Quick Actions filler. */}
      <div className="order-5 grid grid-cols-1 gap-6 md:order-none lg:grid-cols-2">
        <ActiveBlockersPanel tasks={tasks} isLoading={loadingTasks} />
        <RecentActivityFeed
          activities={activity as Activity[] | undefined}
          isLoading={loadingActivity}
        />
      </div>

      {/* Team Health */}
      <section className="order-6 md:order-none">
        <h2 className="text-lg font-semibold mb-4">Team Health</h2>
        <TeamHealthCards
          teams={overview?.health_status}
          isLoading={loadingOverview}
        />
      </section>

      {/* Quick Actions */}
      <section className="order-7 md:order-none">
        <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
        <QuickActionsBar />
      </section>

      {/* Metrics, Alerts, Usage, and Performance Row */}
      <div className="order-8 grid grid-cols-1 gap-6 md:order-none lg:grid-cols-2 xl:grid-cols-4">
        <KeyMetricsPanel
          metrics={overview?.key_metrics}
          isLoading={loadingOverview}
        />
        <AuditorAlertsPanel alerts={flags} isLoading={loadingFlags} />
        <UsageOverviewPanel />
        <ScorecardOverviewPanel />
      </div>
    </div>
  );
}
