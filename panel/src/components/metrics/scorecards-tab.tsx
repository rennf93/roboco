"use client";

import { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useAllMemberScorecards,
  useCeoScorecard,
  useOrgScorecard,
} from "@/hooks/use-observability";
import { useAgents } from "@/hooks/use-agents";
import { AgentRole, type Agent, type MemberScorecard } from "@/types";

function pctOrNa(rate: number | null): string {
  return rate === null ? "n/a" : (rate * 100).toFixed(0) + "%";
}

function numOrNa(value: number | null, digits = 1): string {
  return value === null ? "n/a" : value.toFixed(digits);
}

function hoursOrDash(seconds: number): string {
  return (seconds / 3600).toFixed(1) + "h";
}

/** One member's row. Data comes from the batched useAllMemberScorecards
 * fetch (one request for the whole table) rather than each row self-fetching
 * — ~20 agents used to mean ~20 parallel `/metrics/member/{id}` requests
 * (each 3 DB queries) on every table poll. */
function MemberRow({
  agent,
  data,
}: {
  agent: Agent;
  data: MemberScorecard | undefined;
}) {
  if (!data) {
    return (
      <TableRow>
        <TableCell>{agent.name || agent.slug}</TableCell>
        <TableCell colSpan={8}>
          <Skeleton className="h-4 w-full" />
        </TableCell>
      </TableRow>
    );
  }
  return (
    <TableRow>
      <TableCell className="font-medium">
        {agent.name || agent.slug}
        {data.includes_live_inflight && (
          <Badge variant="outline" className="ml-2 text-xs">
            live
          </Badge>
        )}
      </TableCell>
      <TableCell>{data.tasks_completed}</TableCell>
      <TableCell>{pctOrNa(data.first_pass_yield)}</TableCell>
      <TableCell>{data.active_runtime_hours.toFixed(1)}h</TableCell>
      <TableCell>{numOrNa(data.turns_per_task)}</TableCell>
      <TableCell>{pctOrNa(data.qa_pass_rate)}</TableCell>
      <TableCell>{data.escalations}</TableCell>
      <TableCell>{data.blocked_others}</TableCell>
      <TableCell>{pctOrNa(data.utilization)}</TableCell>
    </TableRow>
  );
}

function OrgSummary() {
  const { data, isLoading, isError } = useOrgScorecard();
  if (isError)
    return (
      <div className="text-muted-foreground text-sm">
        Failed to load organization metrics.
      </div>
    );
  if (isLoading || !data) return <Skeleton className="h-24 w-full" />;
  const cells: [string, string, string?][] = [
    ["Members", String(data.member_count)],
    ["Completed", String(data.tasks_completed)],
    [
      "First-pass yield",
      pctOrNa(data.first_pass_yield),
      "Share of completed tasks that shipped without a QA/PR-gate/PM/CEO bounce",
    ],
    [
      "Throughput/hr",
      numOrNa(data.effort_throughput_per_hour, 2),
      "Tasks completed per hour of active (non-idle) agent runtime",
    ],
    [
      "Active effort",
      data.active_runtime_hours.toFixed(1) + "h",
      "Total hours agents spent actively working — idle time excluded",
    ],
    ["Cost", "$" + data.cost_usd.toFixed(2)],
  ];
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
      {cells.map(([k, v, tip]) => (
        <HelpTip key={k} label={tip}>
          <div>
            <div className="text-2xl font-semibold">{v}</div>
            <div className="text-muted-foreground text-sm">{k}</div>
          </div>
        </HelpTip>
      ))}
    </div>
  );
}

function CeoCard() {
  const { data, isLoading, isError } = useCeoScorecard();
  if (isError)
    return (
      <div className="text-muted-foreground text-sm">
        Failed to load CEO metrics.
      </div>
    );
  if (isLoading || !data) return <Skeleton className="h-24 w-full" />;
  const cells: [string, string, string?][] = [
    ["Approvals", String(data.approval_count)],
    [
      "Approval p50",
      hoursOrDash(data.approval_p50_seconds),
      "Median time from a task reaching your queue to your approval",
    ],
    [
      "Approval p90",
      hoursOrDash(data.approval_p90_seconds),
      "90th percentile — the slowest 10% of approvals took at least this long",
    ],
    ["Unblocks", String(data.unblock_count)],
    [
      "Unblock p50",
      hoursOrDash(data.unblock_p50_seconds),
      "Median time from a task blocking to you unblocking it",
    ],
    [
      "God-mode actions",
      String(data.godmode_actions),
      "Direct admin overrides you made outside the normal approval flow",
    ],
  ];
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
      {cells.map(([k, v, tip]) => (
        <HelpTip key={k} label={tip}>
          <div>
            <div className="text-2xl font-semibold">{v}</div>
            <div className="text-muted-foreground text-sm">{k}</div>
          </div>
        </HelpTip>
      ))}
    </div>
  );
}

export function ScorecardsTabContent() {
  const { data: agents } = useAgents();
  const members = (agents ?? []).filter(
    (a) => a.role !== AgentRole.CEO && a.role !== AgentRole.SYSTEM,
  );
  const { data: scorecards, isError: scorecardsError } =
    useAllMemberScorecards();
  const scorecardById = useMemo(
    () => new Map((scorecards ?? []).map((s) => [s.id, s])),
    [scorecards],
  );
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Organization</CardTitle>
        </CardHeader>
        <CardContent>
          <OrgSummary />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>CEO (you)</CardTitle>
        </CardHeader>
        <CardContent>
          <CeoCard />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Members</CardTitle>
        </CardHeader>
        <CardContent>
          {scorecardsError && (
            <p className="text-muted-foreground mb-2 text-sm">
              Failed to load member scorecards.
            </p>
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Member</TableHead>
                <TableHead>
                  <HelpTip label="Tasks completed">
                    <span>Done</span>
                  </HelpTip>
                </TableHead>
                <TableHead>
                  <HelpTip label="First-pass yield — share of completed tasks shipped without a QA/PR-gate/PM/CEO bounce">
                    <span>FPY</span>
                  </HelpTip>
                </TableHead>
                <TableHead>
                  <HelpTip label="Total hours actively working — idle/waiting time excluded">
                    <span>Effort</span>
                  </HelpTip>
                </TableHead>
                <TableHead>
                  <HelpTip label="Average number of agent turns spent per completed task">
                    <span>Turns/task</span>
                  </HelpTip>
                </TableHead>
                <TableHead>
                  <HelpTip label="Share of this agent's QA reviews that passed on the first attempt">
                    <span>QA pass</span>
                  </HelpTip>
                </TableHead>
                <TableHead>
                  <HelpTip label="Escalations — times this agent's work was escalated up the chain">
                    <span>Escal.</span>
                  </HelpTip>
                </TableHead>
                <TableHead>
                  <HelpTip label="Times this agent's work blocked another agent's progress">
                    <span>Blocked others</span>
                  </HelpTip>
                </TableHead>
                <TableHead>
                  <HelpTip label="Utilization — share of this agent's spawned time spent actively working, not idle">
                    <span>Util.</span>
                  </HelpTip>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((a) => (
                <MemberRow key={a.id} agent={a} data={scorecardById.get(a.id)} />
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
