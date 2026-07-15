"use client";

import { AuditorReport } from "@/types";
import {
  useSendAuditorReport,
  useCreateAuditorReport,
} from "@/hooks/use-dashboard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { ScrollArea } from "@/components/ui/scroll-area";
import { FileText, Send, Eye, Clock, Plus } from "lucide-react";
import { toast } from "sonner";

interface ReportsPanelProps {
  reports: AuditorReport[] | undefined;
  isLoading: boolean;
  onCreateReport?: () => void;
}

function formatDate(timestamp: string): string {
  return new Date(timestamp).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function ReportsPanel({
  reports,
  isLoading,
  onCreateReport,
}: ReportsPanelProps) {
  const sendReport = useSendAuditorReport();
  const createReport = useCreateAuditorReport();

  const handleNewReport = () => {
    if (onCreateReport) {
      onCreateReport();
      return;
    }
    // No parent handler provided — create a draft report directly
    createReport.mutate(
      {
        report_type: "summary",
        title: `New Report — ${new Date().toLocaleDateString()}`,
        summary: "Draft report created from the Reports panel.",
        sections: [],
      },
      {
        onSuccess: () => toast.success("Draft report created"),
        onError: () => toast.error("Failed to create report"),
      },
    );
  };

  const handleSend = async (reportId: string) => {
    try {
      await sendReport.mutateAsync(reportId);
      toast.success("Report sent to CEO");
    } catch {
      toast.error("Failed to send report");
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <HelpTip label="Audit reports for the CEO; drafts stay editable until sent">
            <CardTitle className="text-lg flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Reports
            </CardTitle>
          </HelpTip>
          <HelpTip label="Creates a new draft report you can edit and send below">
            <span>
              <Button
                size="sm"
                onClick={handleNewReport}
                disabled={createReport.isPending}
              >
                <Plus className="h-4 w-4 mr-1" />
                New Report
              </Button>
            </span>
          </HelpTip>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-16" />
            ))}
          </div>
        ) : !reports || reports.length === 0 ? (
          <HelpTip label="No audit reports have been generated yet — use New Report to create one">
            <div className="text-center py-8 text-muted-foreground text-sm">
              <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
              No reports yet
            </div>
          </HelpTip>
        ) : (
          <ScrollArea className="h-[300px] pr-4">
            <div className="space-y-3">
              {reports.map((report) => {
                const isDraft = !report.sent_at;
                return (
                  <div
                    key={report.id}
                    className="flex items-center justify-between p-3 rounded-lg border bg-muted/30"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <HelpTip label="Draft reports aren't visible to the CEO until sent">
                          <Badge variant={isDraft ? "secondary" : "default"}>
                            {isDraft ? "Draft" : "Sent"}
                          </Badge>
                        </HelpTip>
                        <span className="font-medium text-sm truncate">
                          {report.title}
                        </span>
                      </div>
                      <div className="flex items-center gap-4 text-xs text-muted-foreground">
                        <HelpTip label="Report category set when this report was created">
                          <span className="capitalize">
                            {report.report_type}
                          </span>
                        </HelpTip>
                        <HelpTip
                          label={new Date(report.created_at).toLocaleString()}
                        >
                          <span className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            {formatDate(report.created_at)}
                          </span>
                        </HelpTip>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <HelpTip label="View report">
                        <Button variant="ghost" size="sm">
                          <Eye className="h-4 w-4" />
                        </Button>
                      </HelpTip>
                      {isDraft && (
                        <HelpTip label="Sends this draft report to the CEO">
                          <span>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleSend(report.id)}
                              disabled={sendReport.isPending}
                            >
                              <Send className="h-4 w-4 mr-1" />
                              Send
                            </Button>
                          </span>
                        </HelpTip>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
}
