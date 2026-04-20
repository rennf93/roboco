"use client";

import { useState } from "react";
import { AuditorFlag, FlagSeverity } from "@/types";
import { useResolveAuditorFlag } from "@/hooks/use-dashboard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Flag, Plus } from "lucide-react";
import { FlaggedItem } from "./flagged-item";
import { CreateFlagDialog } from "./create-flag-dialog";
import { toast } from "sonner";

interface FlaggedItemsPanelProps {
  flags: AuditorFlag[] | undefined;
  isLoading: boolean;
}

export function FlaggedItemsPanel({ flags, isLoading }: FlaggedItemsPanelProps) {
  const [filter, setFilter] = useState<"all" | "unresolved" | "resolved">("unresolved");
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const resolveFlag = useResolveAuditorFlag();

  // Filter flags
  const filteredFlags = (flags ?? []).filter((f) => {
    if (filter === "unresolved") return !f.resolved_at;
    if (filter === "resolved") return !!f.resolved_at;
    return true;
  });

  // Sort by severity (urgent first) then by date
  const sortedFlags = [...filteredFlags].sort((a, b) => {
    const severityOrder: Record<FlagSeverity, number> = {
      [FlagSeverity.URGENT]: 0,
      [FlagSeverity.WARNING]: 1,
      [FlagSeverity.INFO]: 2,
    };
    const severityDiff = severityOrder[a.severity] - severityOrder[b.severity];
    if (severityDiff !== 0) return severityDiff;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  const unresolvedCount = (flags ?? []).filter((f) => !f.resolved_at).length;

  const handleResolve = async (flagId: string) => {
    try {
      await resolveFlag.mutateAsync({ flagId });
      toast.success("Flag resolved");
    } catch {
      toast.error("Failed to resolve flag");
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-lg flex items-center gap-2">
              <Flag className="h-5 w-5" />
              Flagged Items
            </CardTitle>
            {unresolvedCount > 0 && (
              <Badge variant="destructive">{unresolvedCount}</Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Select value={filter} onValueChange={(v) => setFilter(v as "all" | "unresolved" | "resolved")}>
              <SelectTrigger className="w-auto min-w-24 h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="unresolved">Unresolved</SelectItem>
                <SelectItem value="resolved">Resolved</SelectItem>
                <SelectItem value="all">All</SelectItem>
              </SelectContent>
            </Select>
            <Button size="sm" onClick={() => setCreateDialogOpen(true)}>
              <Plus className="h-4 w-4 mr-1" />
              Flag
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
        ) : sortedFlags.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground text-sm">
            <Flag className="h-8 w-8 mx-auto mb-2 opacity-50" />
            No {filter === "all" ? "" : filter} flags
          </div>
        ) : (
          <ScrollArea className="h-[400px] pr-4">
            <div className="space-y-3">
              {sortedFlags.map((flag) => (
                <FlaggedItem
                  key={flag.id}
                  flag={flag}
                  onResolve={handleResolve}
                />
              ))}
            </div>
          </ScrollArea>
        )}
      </CardContent>

      <CreateFlagDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
      />
    </Card>
  );
}
