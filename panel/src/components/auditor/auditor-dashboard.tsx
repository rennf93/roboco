"use client";

import {
  useAuditorDashboard,
  useAuditorFlags,
  useAuditorReports,
  useCreateAuditorReport,
} from "@/hooks/use-dashboard";
import { QualityMetricsPanel } from "./quality-metrics-panel";
import { FlaggedItemsPanel } from "./flagged-items-panel";
import { ReportsPanel } from "./reports-panel";
import { Button } from "@/components/ui/button";
import { RefreshCw, FileText } from "lucide-react";
import { toast } from "sonner";

export function AuditorDashboard() {
  const {
    data: dashboard,
    isLoading: loadingDashboard,
    refetch,
  } = useAuditorDashboard();
  const { data: flags, isLoading: loadingFlags } = useAuditorFlags();
  const { data: reports, isLoading: loadingReports } = useAuditorReports();
  const createReport = useCreateAuditorReport();

  const handleRefresh = () => {
    refetch();
  };

  const handleGenerateReport = () => {
    createReport.mutate(
      {
        report_type: "audit_summary",
        title: `Audit Summary — ${new Date().toLocaleDateString()}`,
        summary: "Automatically generated audit summary report.",
        sections: [],
      },
      {
        onSuccess: () => toast.success("Audit report generated successfully"),
        onError: () => toast.error("Failed to generate audit report"),
      },
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            Auditor Dashboard
          </h1>
          <p className="text-muted-foreground">
            Quality oversight, flagging, and reporting
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleRefresh}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Button
            onClick={handleGenerateReport}
            disabled={createReport.isPending}
          >
            <FileText className="h-4 w-4 mr-2" />
            Generate Report
          </Button>
        </div>
      </div>

      {/* Quality Metrics */}
      <QualityMetricsPanel
        metrics={dashboard?.metrics}
        isLoading={loadingDashboard}
      />

      {/* Bottom Row: Flagged Items + Reports */}
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-2 2xl:grid-cols-2 gap-6">
        <FlaggedItemsPanel flags={flags} isLoading={loadingFlags} />
        <ReportsPanel reports={reports} isLoading={loadingReports} />
      </div>
    </div>
  );
}
