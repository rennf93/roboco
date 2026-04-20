"use client";

import {
  useAuditorDashboard,
  useAuditorFlags,
  useAuditorReports,
} from "@/hooks/use-dashboard";
import { LiveFeedsPanel } from "./live-feeds-panel";
import { QualityMetricsPanel } from "./quality-metrics-panel";
import { FlaggedItemsPanel } from "./flagged-items-panel";
import { ReportsPanel } from "./reports-panel";
import { Button } from "@/components/ui/button";
import { RefreshCw, FileText } from "lucide-react";

export function AuditorDashboard() {
  const {
    data: dashboard,
    isLoading: loadingDashboard,
    refetch,
  } = useAuditorDashboard();
  const { data: flags, isLoading: loadingFlags } = useAuditorFlags();
  const { data: reports, isLoading: loadingReports } = useAuditorReports();

  const handleRefresh = () => {
    refetch();
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Auditor Dashboard</h1>
          <p className="text-muted-foreground">
            Quality oversight, flagging, and reporting
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleRefresh}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Button>
            <FileText className="h-4 w-4 mr-2" />
            Generate Report
          </Button>
        </div>
      </div>

      {/* Top Row: Live Feeds + Quality Metrics */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <LiveFeedsPanel
          feeds={dashboard?.live_feeds}
          isLoading={loadingDashboard}
        />
        <QualityMetricsPanel
          metrics={dashboard?.metrics}
          isLoading={loadingDashboard}
        />
      </div>

      {/* Bottom Row: Flagged Items + Reports */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <FlaggedItemsPanel flags={flags} isLoading={loadingFlags} />
        <ReportsPanel reports={reports} isLoading={loadingReports} />
      </div>
    </div>
  );
}
