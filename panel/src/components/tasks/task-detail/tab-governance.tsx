"use client";

import { Task } from "@/types";
import { useTaskGovernance } from "@/hooks/use-tasks";
import type {
  GovernanceGateStep,
  GovernanceReportResponse,
} from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { formatAbsoluteTimestamp } from "@/lib/utils";
import { taskStatusDescription } from "../task-status-badge";
import {
  ShieldCheck,
  CheckCircle2,
  XCircle,
  Clock,
  CircleDashed,
  AlertTriangle,
  type LucideIcon,
} from "lucide-react";

interface TabGovernanceProps {
  task: Task;
}

// The ordered gates the chain walks (conventions -> self-verification -> QA
// -> PR-gate -> PM review -> CEO approval), rendered in the backend's order —
// the report always lists every gate, so no sorting happens here.
const GATE_LABEL: Record<string, string> = {
  conventions: "Conventions",
  self_verification: "Self-verification",
  qa: "QA",
  pr_gate: "PR gate",
  pm_review: "PM review",
  ceo_approval: "CEO approval",
};

// Per-status verdict rendering (an inline map per the task-detail tabs'
// convention — see tab-findings' SEVERITY_CLASS). Unknown statuses fall back
// to the muted not_reached treatment so a new backend state degrades
// gracefully instead of crashing the tab.
const STATUS_META: Record<
  string,
  {
    label: string;
    badge: string;
    icon: LucideIcon;
    iconWrap: string;
    iconClass: string;
    description: string;
  }
> = {
  passed: {
    label: "passed",
    badge:
      "border-green-300 text-green-700 dark:border-green-800 dark:text-green-300",
    icon: CheckCircle2,
    iconWrap: "bg-green-100 dark:bg-green-900",
    iconClass: "text-green-600 dark:text-green-300",
    description: "This gate evaluated and passed.",
  },
  failed: {
    label: "failed",
    badge: "border-red-300 text-red-700 dark:border-red-800 dark:text-red-300",
    icon: XCircle,
    iconWrap: "bg-red-100 dark:bg-red-900",
    iconClass: "text-red-600 dark:text-red-300",
    description:
      "This gate rejected the work — the task bounced back for revision.",
  },
  pending: {
    label: "pending",
    badge:
      "border-amber-300 text-amber-700 dark:border-amber-800 dark:text-amber-300",
    icon: Clock,
    iconWrap: "bg-amber-100 dark:bg-amber-900",
    iconClass: "text-amber-600 dark:text-amber-300",
    description: "The gate was reached but hasn't been evaluated yet.",
  },
  not_reached: {
    label: "not reached",
    badge: "border-border text-muted-foreground",
    icon: CircleDashed,
    iconWrap: "bg-muted",
    iconClass: "text-muted-foreground",
    description:
      "The task hasn't progressed far enough to reach this gate yet.",
  },
};

const ORIGIN_LABEL: Record<string, string> = {
  qa: "QA",
  pr_gate: "PR Review",
  pm: "PM",
  ceo: "CEO",
};

function GateStepRow({
  step,
  isLast,
}: {
  step: GovernanceGateStep;
  isLast: boolean;
}) {
  const meta = STATUS_META[step.status] ?? STATUS_META.not_reached;
  const Icon = meta.icon;
  return (
    <li className="relative flex gap-3 pb-6 last:pb-0">
      {/* Connector from this marker down to the next one (hidden on the last
          entry — the chain ends there). */}
      {!isLast && (
        <span
          aria-hidden
          className="absolute left-[11px] top-7 bottom-0 w-px bg-border"
        />
      )}
      <span
        className={`relative flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${meta.iconWrap}`}
      >
        <Icon className={`h-3.5 w-3.5 ${meta.iconClass}`} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">
            {GATE_LABEL[step.gate] ?? step.gate}
          </span>
          <HelpTip label={meta.description}>
            <Badge variant="outline" className={meta.badge}>
              {meta.label}
            </Badge>
          </HelpTip>
          {step.timestamp && (
            <span className="ml-auto text-xs text-muted-foreground">
              {formatAbsoluteTimestamp(step.timestamp)}
            </span>
          )}
        </div>
        {step.detail && (
          <p className="mt-1 text-xs text-muted-foreground">{step.detail}</p>
        )}
      </div>
    </li>
  );
}

function MetricCard({
  label,
  tip,
  children,
}: {
  label: string;
  tip: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardContent className="pt-4 space-y-1">
        <HelpTip label={tip}>
          <p className="text-xs uppercase tracking-wide text-muted-foreground w-fit">
            {label}
          </p>
        </HelpTip>
        <div className="flex flex-wrap items-center gap-2">{children}</div>
      </CardContent>
    </Card>
  );
}

function ConventionsVerdict({ report }: { report: GovernanceReportResponse }) {
  return (
    <>
      {report.conventions_block_count > 0 && (
        <HelpTip label="Conventions violations that refuse submission until fixed or waived in-branch.">
          <Badge className="bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">
            {report.conventions_block_count} block finding
            {report.conventions_block_count === 1 ? "" : "s"}
          </Badge>
        </HelpTip>
      )}
      {report.conventions_warn_count > 0 && (
        <HelpTip label="Non-blocking conventions warnings recorded against the task.">
          <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300">
            {report.conventions_warn_count} warn finding
            {report.conventions_warn_count === 1 ? "" : "s"}
          </Badge>
        </HelpTip>
      )}
      {report.conventions_block_count === 0 &&
        report.conventions_warn_count === 0 && (
          <HelpTip label="No conventions findings were recorded for this task.">
            <Badge className="bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300">
              clean
            </Badge>
          </HelpTip>
        )}
    </>
  );
}

export function TabGovernance({ task }: TabGovernanceProps) {
  const { data, isLoading, isError } = useTaskGovernance(task.id);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <div className="grid gap-4 sm:grid-cols-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="py-12 text-center text-muted-foreground">
        <AlertTriangle className="mx-auto mb-4 h-12 w-12 opacity-50" />
        <p>Couldn&apos;t load the governance report.</p>
      </div>
    );
  }

  // No data without an error means the query never ran (no task id) — the
  // same empty treatment as a task that hasn't reached any gate yet.
  if (!data || data.gate_chain.every((s) => s.status === "not_reached")) {
    return (
      <div className="py-12 text-center text-muted-foreground">
        <ShieldCheck className="mx-auto mb-4 h-12 w-12 opacity-50" />
        <p>No governance activity yet.</p>
        <p className="mt-2 text-sm">
          Gate evaluations appear here once the task starts moving through
          review.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <MetricCard
          label="Rework"
          tip="How many times this task bounced back for revision (QA / PR review / PM / CEO)."
        >
          <span className="text-2xl font-semibold">
            {data.revision_count}{" "}
            {data.revision_count === 1 ? "revision" : "revisions"}
          </span>
        </MetricCard>
        <MetricCard
          label="Conventions"
          tip="The architectural-standard verdict from this task's conventions check."
        >
          <ConventionsVerdict report={data} />
        </MetricCard>
        <MetricCard
          label="Task status"
          tip={taskStatusDescription(task.status)}
        >
          <Badge variant="outline">{data.task_status}</Badge>
        </MetricCard>
      </div>

      <Card>
        <CardContent className="pt-4">
          <HelpTip label="The ordered gates a task passes from conventions to CEO approval. A failed gate bounced the task back for revision.">
            <h3 className="text-sm font-semibold mb-4 w-fit">
              Quality-gate chain
            </h3>
          </HelpTip>
          <ol className="ml-1">
            {data.gate_chain.map((step, i) => (
              <GateStepRow
                key={step.gate}
                step={step}
                isLast={i === data.gate_chain.length - 1}
              />
            ))}
          </ol>
        </CardContent>
      </Card>

      {data.findings_summary.length > 0 && (
        <Card>
          <CardContent className="pt-4 space-y-2">
            <HelpTip label="Per-reviewer counts from the revision-findings ledger. Closed combines addressed, verified, and waived findings.">
              <h3 className="text-sm font-semibold w-fit">
                Revision findings
              </h3>
            </HelpTip>
            <div className="flex flex-wrap gap-2">
              {data.findings_summary.map((s) => (
                <Badge key={s.origin} variant="outline">
                  {ORIGIN_LABEL[s.origin] ?? s.origin}: {s.open} open ·{" "}
                  {s.addressed + s.verified + s.waived} closed
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
