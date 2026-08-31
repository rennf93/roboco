"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { usePortfolio } from "@/hooks/use-portfolio";
import type { PortfolioCard } from "@/types";
import { AlertTriangle, Clock, Coins, ListChecks, RotateCcw } from "lucide-react";

// Formatting follows the existing dashboard-card precedents: ratio → rounded %
// (key-metrics-panel), hours → `.toFixed(1) + "h"` (scorecard-overview-panel),
// currency → "$" + `.toFixed(2)` (usage-overview-panel, scorecard-overview-panel),
// null → an em dash.
function fmtHours(value: number | null): string {
  return value === null ? "—" : value.toFixed(1) + "h";
}

function fmtPct(rate: number): string {
  return Math.round(rate * 100) + "%";
}

function fmtUsd(value: number): string {
  return "$" + value.toFixed(2);
}

interface MetricRowProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  tip: string;
}

function MetricRow({ icon, label, value, tip }: MetricRowProps) {
  return (
    <HelpTip label={tip}>
      <div className="flex items-center justify-between py-1">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          {icon}
          {label}
        </div>
        <span className="text-sm font-semibold tabular-nums">{value}</span>
      </div>
    </HelpTip>
  );
}

function ProjectCard({ project }: { project: PortfolioCard }) {
  return (
    <Link
      href={`/tasks?project=${encodeURIComponent(project.project_slug)}`}
      className="block"
      prefetch={false}
    >
      <Card className="h-full transition-colors hover:bg-accent/50">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-lg">
            {project.project_name}
          </CardTitle>
        </CardHeader>
        <CardContent className="divide-y">
          <MetricRow
            icon={<ListChecks className="h-4 w-4" />}
            label="Active tasks"
            value={String(project.active_task_count)}
            tip="Tasks currently in flight (non-backlog, non-terminal)"
          />
          <MetricRow
            icon={<Clock className="h-4 w-4" />}
            label="Median lead time"
            value={fmtHours(project.median_lead_time_hours)}
            tip="Median lead time of completed tasks in the last 30 days (hours)"
          />
          <MetricRow
            icon={<RotateCcw className="h-4 w-4" />}
            label="Rework rate"
            value={fmtPct(project.rework_rate)}
            tip="Share of completed tasks that bounced through at least one revision"
          />
          <MetricRow
            icon={<AlertTriangle className="h-4 w-4" />}
            label="Open findings"
            value={String(project.open_findings_count)}
            tip="Open review findings across the project's tasks"
          />
          <MetricRow
            icon={<Coins className="h-4 w-4" />}
            label="Budget burn (mo)"
            value={fmtUsd(project.monthly_budget_burn_usd)}
            tip="Agent-token spend this calendar month (USD)"
          />
        </CardContent>
      </Card>
    </Link>
  );
}

function ProjectCardSkeleton() {
  return (
    <Card>
      <CardHeader className="pb-3">
        <Skeleton className="h-5 w-1/3" />
      </CardHeader>
      <CardContent className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-6" />
        ))}
      </CardContent>
    </Card>
  );
}

/**
 * CEO portfolio view: one card per project with its delivery metrics, in the
 * endpoint's most-active-first order (GET /dashboard/portfolio sorts by
 * active_task_count descending server-side — do not re-sort client-side).
 * Each card drills down to that project's task list. Data comes from
 * usePortfolio, which the backend CEO-gates; the caller additionally wraps the
 * section in CeoGate so agent-role sessions never mount this component.
 */
export function PortfolioCards() {
  const { data: projects, isLoading, isError } = usePortfolio();

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <ProjectCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="text-sm text-muted-foreground">
        Failed to load portfolio metrics.
      </div>
    );
  }

  if (!projects || projects.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-muted-foreground">
        No projects in the portfolio yet.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {projects.map((project) => (
        <ProjectCard key={project.project_id} project={project} />
      ))}
    </div>
  );
}