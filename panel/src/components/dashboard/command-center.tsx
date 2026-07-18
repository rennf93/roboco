"use client";

import { useEffect } from "react";
import {
  useCeoOverview,
  useAuditorFlags,
  useRecentActivity,
} from "@/hooks/use-dashboard";
import { useTasks } from "@/hooks/use-tasks";
import { useUsageTimeSeries } from "@/hooks/use-usage";
import { usePageRefresh } from "@/hooks";
import { TeamHealthCards } from "./team-health-cards";
import { KeyMetricsPanel } from "./key-metrics-panel";
import { AuditorAlertsPanel } from "./auditor-alerts-panel";
import { ActiveBlockersPanel } from "./active-blockers-panel";
import { RecentActivityFeed } from "./recent-activity-feed";
import { QuickActionsCard } from "./quick-actions-card";
import { CeoApprovalQueue } from "./ceo-approval-queue";
import { PrReviewQueue } from "./pr-review-queue";
import { ReleaseProposalCard } from "./release-proposal-card";
import { PlaybookReviewQueue } from "./playbook-review-queue";
import { SocialSummaryCard } from "./social-summary-card";
import { RoadmapReviewQueue } from "./roadmap-review-queue";
import { StrategySignalsPanel } from "./strategy-signals-panel";
import type { Activity } from "./activity-item";
import { Button } from "@/components/ui/button";
import { UsageOverviewPanel } from "./usage-overview-panel";
import { ScorecardOverviewPanel } from "./scorecard-overview-panel";
import { CostTrendChart } from "./cost-trend-chart";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Settings, AlertCircle } from "lucide-react";
import Link from "next/link";
import { HelpTip } from "@/components/ui/help-tip";

const SETTINGS_LABEL = "Open settings";

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
  const {
    data: costTrend,
    isLoading: loadingCostTrend,
    refetch: refetchCostTrend,
  } = useUsageTimeSeries("7d");

  const { register, unregister } = usePageRefresh();

  useEffect(() => {
    const callbacks = [
      () => {
        void refetchOverview();
      },
      () => {
        void refetchFlags();
      },
      () => {
        void refetchTasks();
      },
      () => {
        void refetchActivity();
      },
      () => {
        void refetchCostTrend();
      },
    ];
    callbacks.forEach((cb) => register(cb));
    return () => {
      callbacks.forEach((cb) => unregister(cb));
    };
  }, [
    register,
    unregister,
    refetchOverview,
    refetchFlags,
    refetchTasks,
    refetchActivity,
    refetchCostTrend,
  ]);

  const hasError = errorOverview || errorFlags || errorTasks || errorActivity;

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            RoboCo Command Center
          </h1>
          <p className="text-muted-foreground">
            Complete visibility into all operations
          </p>
        </div>
        <div className="flex items-center gap-2">
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Link href="/settings" prefetch={false}>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={SETTINGS_LABEL}
                    title={SETTINGS_LABEL}
                  >
                    <Settings className="h-5 w-5" />
                  </Button>
                </Link>
              </TooltipTrigger>
              <TooltipContent>{SETTINGS_LABEL}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
      </div>

      {/* Error indicator */}
      {hasError && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Some data failed to load. Use the header refresh button to try again.
        </div>
      )}

      {/* Section 1: Quick Actions + the four key cards */}
      <section>
        <HelpTip label="One-click shortcuts to the pages you visit most often">
          <h2 className="text-lg font-semibold mb-4 inline-block">
            Quick Actions
          </h2>
        </HelpTip>
        <QuickActionsCard />
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 xl:grid-cols-4">
        <KeyMetricsPanel
          metrics={overview?.key_metrics}
          isLoading={loadingOverview}
        />
        <AuditorAlertsPanel alerts={flags} isLoading={loadingFlags} />
        <UsageOverviewPanel />
        <ScorecardOverviewPanel />
      </div>

      <CostTrendChart data={costTrend} isLoading={loadingCostTrend} />

      {/* Section 2: Team Health (team cards + Task Intake + Secretary) */}
      <section>
        <HelpTip label="Per-team blocked ratio and throughput, plus the on-demand agents">
          <h2 className="text-lg font-semibold mb-4 inline-block">
            Team Health
          </h2>
        </HelpTip>
        <TeamHealthCards
          teams={overview?.health_status}
          isLoading={loadingOverview}
        />
      </section>

      {/* Section 3: everything else */}

      {/* CEO Approval Queue + Strategy Signals - side-by-side on lg+ */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <CeoApprovalQueue />
        <StrategySignalsPanel />
      </div>

      {/* External-PR review decision queue + Social summary - side-by-side
          on lg+ (both hidden-when-empty / compact, so pairing them avoids
          two near-empty full-width rows) */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <PrReviewQueue />
        <SocialSummaryCard />
      </div>

      {/* Gated release proposal (hidden when none open) */}
      <ReleaseProposalCard />

      {/* Playbook review queue (hidden when no drafts) */}
      <PlaybookReviewQueue />

      {/* Board roadmap queue (hidden when no cycle authored) */}
      <RoadmapReviewQueue />

      {/* Blockers and Activity Row */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ActiveBlockersPanel tasks={tasks} isLoading={loadingTasks} />
        <RecentActivityFeed
          activities={activity as Activity[] | undefined}
          isLoading={loadingActivity}
        />
      </div>
    </div>
  );
}
