"use client";

import { useQuery } from "@tanstack/react-query";
import { periscopeApi } from "@/lib/api";
import type { MarketBrief } from "@/lib/api/periscope";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";

// ---------------------------------------------------------------------------
// Read-only skeleton placeholder shaped like a brief card
// ---------------------------------------------------------------------------

function BriefCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-64" />
        <Skeleton className="h-4 w-40 mt-1" />
      </CardHeader>
      <CardContent className="space-y-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// One brief, read-only — headline/findings/sources/threats/opportunities.
// No approve/reject UI: a market brief is a report, not a queue item.
// ---------------------------------------------------------------------------

function BriefCard({ brief }: { brief: MarketBrief }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">{brief.headline}</CardTitle>
        {brief.completed_at && (
          <p className="text-xs text-muted-foreground">
            Filed {new Date(brief.completed_at).toLocaleString()}
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {brief.findings.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Findings
            </p>
            <ul className="space-y-2">
              {brief.findings.map((f) => (
                <li key={f.id} className="text-sm">
                  <p className="whitespace-pre-wrap">{f.claim}</p>
                  <p className="text-xs text-muted-foreground">
                    {f.relevance} —{" "}
                    <a
                      href={f.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline"
                    >
                      source
                    </a>
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}
        {brief.threats.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Threats
            </p>
            <div className="flex flex-wrap gap-1">
              {brief.threats.map((t) => (
                <HelpTip key={t} label={t}>
                  <Badge variant="destructive" className="max-w-full truncate">
                    {t}
                  </Badge>
                </HelpTip>
              ))}
            </div>
          </div>
        )}
        {brief.opportunities.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Opportunities
            </p>
            <div className="flex flex-wrap gap-1">
              {brief.opportunities.map((o) => (
                <HelpTip key={o} label={o}>
                  <Badge
                    variant="secondary"
                    className="max-w-full truncate bg-green-600/10 text-green-700"
                  >
                    {o}
                  </Badge>
                </HelpTip>
              ))}
            </div>
          </div>
        )}
        {brief.positioning_note && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Positioning
            </p>
            <p className="text-sm whitespace-pre-wrap">
              {brief.positioning_note}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

export function MarketBriefsTab() {
  const {
    data: briefs = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["periscope", "briefs"],
    queryFn: () => periscopeApi.listBriefs(),
    refetchInterval: 30000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Market Briefs</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-4">
            <BriefCardSkeleton />
            <BriefCardSkeleton />
          </div>
        ) : isError ? (
          <OfflineState
            title="Failed to load market briefs"
            description="Could not reach the orchestrator API. Check the backend is running."
            onRetry={() => void refetch()}
          />
        ) : briefs.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No market briefs filed yet. The Head of Marketing files one
            weekly, once Periscope is enabled — they appear here as reports,
            not a queue to act on.
          </p>
        ) : (
          <div className="space-y-4">
            {briefs.map((b) => (
              <BriefCard key={b.task_id} brief={b} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
