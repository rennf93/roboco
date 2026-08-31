"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";
import {
  ResponsiveTable,
  ResponsiveTableCardList,
  ResponsiveTableCard,
  ResponsiveTableCardRow,
} from "@/components/ui/responsive-table";
import { useZeroProgressSpawnWaste } from "@/hooks/use-observability";

const ZERO_PROGRESS_TIP =
  "Ended, task-scoped agent spawn sessions that advanced nothing on their task within the session's own window - no status advance, commit, progress update, or journal entry - and what that spend cost. NOT the same as the Metrics page's 'Spawn Waste' card, which flags zero output tokens (GET /usage/spawn-waste). A session produces output but zero progress; that is the waste this card measures.";

function label(team: string): string {
  return team
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function pct(rate: number): string {
  return (rate * 100).toFixed(1) + "%";
}

function usd(amount: number): string {
  return "$" + amount.toFixed(2);
}

// Mirrors ReworkCard's by-agent table: desktop table, card list on narrow
// viewports.
function AgentTable({
  rows,
}: {
  rows: { agent_slug: string; sessions: number; zero_progress_sessions: number; zero_progress_cost_usd: number; rate: number }[];
}) {
  return (
    <ResponsiveTable
      table={
        <table className="w-full text-xs">
          <thead className="text-muted-foreground">
            <tr className="text-left">
              <th className="py-1 font-medium">Agent</th>
              <th className="py-1 text-right font-medium">
                <HelpTip label="Total ended, task-scoped sessions for this agent in the window">
                  <span>Sessions</span>
                </HelpTip>
              </th>
              <th className="py-1 text-right font-medium">
                <HelpTip label="Of those, the sessions that advanced nothing on their task in the session's own window">
                  <span>Zero-progress</span>
                </HelpTip>
              </th>
              <th className="py-1 text-right font-medium">
                <HelpTip label="Spawn spend billed to this agent's zero-progress sessions">
                  <span>Cost</span>
                </HelpTip>
              </th>
              <th className="py-1 text-right font-medium">
                <HelpTip label="Share of this agent's ended, task-scoped sessions that were zero-progress">
                  <span>Rate</span>
                </HelpTip>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 8).map((a) => (
              <tr key={a.agent_slug} className="border-t border-border/50">
                <td className="py-1">{a.agent_slug}</td>
                <td className="py-1 text-right tabular-nums">{a.sessions}</td>
                <td className="py-1 text-right tabular-nums">
                  {a.zero_progress_sessions}
                </td>
                <td className="py-1 text-right tabular-nums">
                  {usd(a.zero_progress_cost_usd)}
                </td>
                <td className="py-1 text-right tabular-nums">{pct(a.rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
      cards={
        <ResponsiveTableCardList>
          {rows.slice(0, 8).map((a) => (
            <ResponsiveTableCard key={a.agent_slug}>
              <span className="text-sm font-medium">{a.agent_slug}</span>
              <div className="mt-2 divide-y">
                <ResponsiveTableCardRow label="Sessions">
                  <span className="tabular-nums">{a.sessions}</span>
                </ResponsiveTableCardRow>
                <ResponsiveTableCardRow label="Zero-progress">
                  <span className="tabular-nums">
                    {a.zero_progress_sessions}
                  </span>
                </ResponsiveTableCardRow>
                <ResponsiveTableCardRow label="Cost">
                  <span className="tabular-nums">
                    {usd(a.zero_progress_cost_usd)}
                  </span>
                </ResponsiveTableCardRow>
                <ResponsiveTableCardRow label="Rate">
                  <span className="tabular-nums">{pct(a.rate)}</span>
                </ResponsiveTableCardRow>
              </div>
            </ResponsiveTableCard>
          ))}
        </ResponsiveTableCardList>
      }
    />
  );
}

export function ZeroProgressSpawnWasteCard() {
  const { data, isLoading, isError } = useZeroProgressSpawnWaste(30);
  return (
    <Card>
      <CardHeader className="pb-2">
        <HelpTip label={ZERO_PROGRESS_TIP}>
          <CardTitle className="text-base">
            Zero-Progress Spawn Sessions (30d)
          </CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent className="space-y-4">
        {isError ? (
          <p className="text-sm text-muted-foreground py-8 text-center">
            Failed to load spawn-waste metrics.
          </p>
        ) : isLoading || !data ? (
          <Skeleton className="h-52 w-full" />
        ) : data.total_sessions === 0 ? (
          <p className="text-sm text-muted-foreground py-8 text-center">
            No ended, task-scoped spawn sessions in this window yet.
          </p>
        ) : (
          <>
            <div className="flex items-baseline gap-3">
              <span className="text-3xl font-bold tabular-nums">
                {pct(data.zero_progress_cost_share)}
              </span>
              <span className="text-sm text-muted-foreground">
                {data.zero_progress_sessions} of {data.total_sessions} sessions
                advanced nothing · {usd(data.zero_progress_cost_usd)} of{" "}
                {usd(data.total_cost_usd)} spawn cost
              </span>
            </div>
            {data.by_team.length > 0 && (
              <div
                className="flex flex-wrap gap-2 text-xs"
                data-testid="zero-progress-by-team"
              >
                {data.by_team.map((t) => (
                  <Badge
                    key={t.team}
                    variant="secondary"
                    className="tabular-nums"
                  >
                    {label(t.team)} {t.zero_progress_sessions}/{t.sessions} ·{" "}
                    {usd(t.zero_progress_cost_usd)} · {pct(t.rate)}
                  </Badge>
                ))}
              </div>
            )}
            {data.by_agent.length > 0 ? (
              <AgentTable rows={data.by_agent} />
            ) : (
              <p className="text-sm text-muted-foreground text-center">
                No per-agent breakdown rows yet.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}