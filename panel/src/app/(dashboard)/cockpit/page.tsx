"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cockpitApi } from "@/lib/api/cockpit";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-2xl font-semibold">{value}</p>
    </div>
  );
}

export default function CockpitPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["cockpit", "summary"],
    queryFn: () => cockpitApi.summary(),
    refetchInterval: 30000,
  });

  if (isLoading || !data) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading the cockpit…
      </div>
    );
  }

  const cap = data.spend.monthly_budget_cap_usd;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Cockpit</h1>
          <p className="text-muted-foreground">
            Is the business winning, what&apos;s happening, what needs you.
          </p>
        </div>
        <Badge variant="secondary" title="Performance is a proxy until real launches">
          basis: {data.basis}
        </Badge>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>North star</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm">
            {data.north_star || "No north star set yet — define it in Company Goals."}
          </p>
          {data.objectives.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {data.objectives.map((o, i) => (
                <li key={i}>{JSON.stringify(o)}</li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="In flight" value={data.delivery.in_flight} />
        <Stat label="Blocked" value={data.delivery.blocked} />
        <Stat label="Awaiting your approval" value={data.delivery.awaiting_ceo} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Spend (30 days)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-center gap-3">
            <span className="text-2xl font-semibold">
              ${data.spend.spend_30d_usd.toFixed(2)}
            </span>
            {cap != null && (
              <span className="text-sm text-muted-foreground">
                / ${cap.toFixed(2)} cap
              </span>
            )}
            {data.spend.over_budget && (
              <Badge variant="destructive">
                <AlertTriangle className="mr-1 h-3 w-3" /> over budget
              </Badge>
            )}
          </div>
          {data.spend.projected_monthly_usd != null && (
            <p className="text-xs text-muted-foreground">
              Projected this month: ${data.spend.projected_monthly_usd.toFixed(2)}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Needs your attention</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {data.pending_pitches > 0 && (
            <p className="text-sm">
              {data.pending_pitches} pitch(es) awaiting your approval.
            </p>
          )}
          {data.signals.length === 0 && data.pending_pitches === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing needs you right now.
            </p>
          ) : (
            data.signals.map((s, i) => (
              <div key={i} className="rounded-lg border p-3">
                <p className="text-sm font-medium">{s.summary}</p>
                <p className="text-xs text-muted-foreground">{s.detail}</p>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
