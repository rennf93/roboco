"use client";

import { AuditorReport } from "@/types";
import { useSendAuditorReport } from "@/hooks/use-dashboard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
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

export function ReportsPanel({ reports, isLoading, onCreateReport }: ReportsPanelProps) {
  const sendReport = useSendAuditorReport();

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
          <CardTitle className="text-lg flex items-center gap-2">
            <FileText className="h-5 w-5" />
            Reports
          </CardTitle>
          <Button size="sm" onClick={onCreateReport}>
            <Plus className="h-4 w-4 mr-1" />
            New Report
          </Button>
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
          <div className="text-center py-8 text-muted-foreground text-sm">
            <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
            No reports yet
          </div>
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
                        <Badge variant={isDraft ? "secondary" : "default"}>
                          {isDraft ? "Draft" : "Sent"}
                        </Badge>
                        <span className="font-medium text-sm truncate">
                          {report.title}
                        </span>
                      </div>
                      <div className="flex items-center gap-4 text-xs text-muted-foreground">
                        <span className="capitalize">{report.report_type}</span>
                        <span className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {formatDate(report.created_at)}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <Button variant="ghost" size="sm">
                        <Eye className="h-4 w-4" />
                      </Button>
                      {isDraft && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleSend(report.id)}
                          disabled={sendReport.isPending}
                        >
                          <Send className="h-4 w-4 mr-1" />
                          Send
                        </Button>
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
