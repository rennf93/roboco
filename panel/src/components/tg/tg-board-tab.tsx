"use client";

import { useEffect, useMemo, useState } from "react";
import { useTasks } from "@/hooks/use-tasks";
import { TgTaskSheet } from "@/components/tg/tg-task-sheet";
import { isTgDemoMode } from "@/lib/telegram/demo";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { TaskStatus, type Task } from "@/types";
import { TG_CARD, TgRow, TgRowIcon, TgSection } from "@/components/tg/ui";
import { Skeleton } from "@/components/ui/skeleton";
import { ChevronDown } from "lucide-react";
import {
  ArrowCounterClockwise,
  CheckCircle,
  Circle,
  CircleDashed,
  ClipboardText,
  Crown,
  FileText,
  GitPullRequest,
  Hourglass,
  ListChecks,
  PauseCircle,
  UsersThree,
  Warning,
  XCircle,
} from "@phosphor-icons/react";
import { cn } from "@/lib/utils";

type GroupKey = "needs_you" | "in_review" | "in_flight" | "queued" | "done";

/** Grouping order doubles as render order — the actionable half of the
 * lifecycle first, the collapsed archive last. */
const GROUP_ORDER: GroupKey[] = [
  "needs_you",
  "in_review",
  "in_flight",
  "queued",
  "done",
];

const GROUP_STATUSES: Record<GroupKey, TaskStatus[]> = {
  needs_you: [TaskStatus.AWAITING_CEO_APPROVAL, TaskStatus.BLOCKED],
  in_review: [
    TaskStatus.AWAITING_QA,
    TaskStatus.AWAITING_DOCUMENTATION,
    TaskStatus.AWAITING_PR_REVIEW,
    TaskStatus.AWAITING_PM_REVIEW,
  ],
  in_flight: [
    TaskStatus.CLAIMED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.VERIFYING,
    TaskStatus.NEEDS_REVISION,
    TaskStatus.PAUSED,
  ],
  queued: [TaskStatus.PENDING, TaskStatus.BACKLOG],
  done: [TaskStatus.COMPLETED, TaskStatus.CANCELLED],
};

const GROUP_LABELS: Record<GroupKey, string> = {
  needs_you: "Needs you",
  in_review: "In review",
  in_flight: "In flight",
  queued: "Queued",
  done: "Done",
};

const GROUP_CHIP_LABEL: Record<GroupKey, string> = {
  needs_you: "needs you",
  in_review: "in review",
  in_flight: "in flight",
  queued: "queued",
  done: "done",
};

const GROUP_TONE: Record<
  GroupKey,
  "rose" | "violet" | "sky" | "muted" | "emerald"
> = {
  needs_you: "rose",
  in_review: "violet",
  in_flight: "sky",
  queued: "muted",
  done: "emerald",
};

const STATUS_ICON: Partial<Record<TaskStatus, typeof Circle>> = {
  [TaskStatus.BACKLOG]: ListChecks,
  [TaskStatus.PENDING]: Hourglass,
  [TaskStatus.CLAIMED]: Circle,
  [TaskStatus.IN_PROGRESS]: CircleDashed,
  [TaskStatus.BLOCKED]: Warning,
  [TaskStatus.PAUSED]: PauseCircle,
  [TaskStatus.VERIFYING]: ClipboardText,
  [TaskStatus.NEEDS_REVISION]: ArrowCounterClockwise,
  [TaskStatus.AWAITING_QA]: ClipboardText,
  [TaskStatus.AWAITING_DOCUMENTATION]: FileText,
  [TaskStatus.AWAITING_PR_REVIEW]: GitPullRequest,
  [TaskStatus.AWAITING_PM_REVIEW]: UsersThree,
  [TaskStatus.AWAITING_CEO_APPROVAL]: Crown,
  [TaskStatus.COMPLETED]: CheckCircle,
  [TaskStatus.CANCELLED]: XCircle,
};

function groupTasks(tasks: Task[]): Record<GroupKey, Task[]> {
  const out: Record<GroupKey, Task[]> = {
    needs_you: [],
    in_review: [],
    in_flight: [],
    queued: [],
    done: [],
  };
  for (const task of tasks) {
    const key = GROUP_ORDER.find((g) =>
      GROUP_STATUSES[g].includes(task.status),
    );
    if (key) out[key].push(task);
  }
  return out;
}

function PipelineHeader({ groups }: { groups: Record<GroupKey, Task[]> }) {
  const chips = GROUP_ORDER.filter((k) => groups[k].length > 0);
  return (
    <div className={cn(TG_CARD, "p-4")}>
      <p className="text-[13px] font-semibold text-foreground/90">Pipeline</p>
      <div className="mt-2 flex gap-1.5 overflow-x-auto">
        {chips.map((k) => (
          <span
            key={k}
            className="shrink-0 rounded-full bg-muted px-2.5 py-1 text-xs font-medium tabular-nums text-muted-foreground"
          >
            {groups[k].length} {GROUP_CHIP_LABEL[k]}
          </span>
        ))}
      </div>
    </div>
  );
}

function TaskRow({
  task,
  tone,
  onOpen,
}: {
  task: Task;
  tone: string;
  onOpen: (task: Task) => void;
}) {
  const Icon = STATUS_ICON[task.status] ?? Circle;
  const assignee = task.assigned_to
    ? getAgentDisplayName(task.assigned_to)
    : "Unassigned";
  return (
    <TgRow
      leading={<TgRowIcon icon={Icon} tone={tone} />}
      title={task.title}
      meta={`${assignee} · ${task.team}`}
      onPress={() => onOpen(task)}
    />
  );
}

/** Done is collapsed by default — a completed/cancelled tally that expands
 * into the 20 most recently updated, rather than every terminal task ever. */
function DoneSection({
  tasks,
  onOpen,
}: {
  tasks: Task[];
  onOpen: (task: Task) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const completed = tasks.filter(
    (t) => t.status === TaskStatus.COMPLETED,
  ).length;
  const cancelled = tasks.filter(
    (t) => t.status === TaskStatus.CANCELLED,
  ).length;
  const recent = useMemo(
    () =>
      [...tasks]
        .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))
        .slice(0, 20),
    [tasks],
  );

  return (
    <TgSection title={GROUP_LABELS.done}>
      {!expanded ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="flex min-h-11 w-full items-center justify-between text-sm text-muted-foreground"
        >
          <span>
            {completed} completed · {cancelled} cancelled
          </span>
          <ChevronDown className="h-4 w-4" />
        </button>
      ) : (
        <div className="divide-y divide-white/[0.04]">
          {recent.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              tone={GROUP_TONE.done}
              onOpen={onOpen}
            />
          ))}
        </div>
      )}
    </TgSection>
  );
}

/** Cockpit Board tab — every task grouped by lifecycle stage, tapping any
 * row opens the read-only task sheet. Demo mode swaps in the canned
 * fixture list, lazily imported so it stays out of the prod bundle. */
export function TgBoardTab() {
  const [selected, setSelected] = useState<Task | null>(null);
  const [demoTasks, setDemoTasks] = useState<Task[] | undefined>(undefined);

  useEffect(() => {
    if (!isTgDemoMode()) return;
    void import("@/lib/telegram/demo-data").then((m) =>
      setDemoTasks(m.DEMO_TASKS),
    );
  }, []);

  const { data: fetched, isLoading } = useTasks({ limit: 200 });
  const tasks = useMemo(() => demoTasks ?? fetched ?? [], [demoTasks, fetched]);
  const groups = useMemo(() => groupTasks(tasks), [tasks]);

  if (isLoading && demoTasks === undefined && !isTgDemoMode()) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div
        className={cn(
          TG_CARD,
          "flex flex-col items-center gap-2 p-8 text-center text-muted-foreground",
        )}
      >
        <ListChecks className="h-8 w-8 opacity-50" />
        <p className="text-sm">No tasks yet</p>
      </div>
    );
  }

  return (
    <>
      <div className="tg-stagger space-y-3">
        <PipelineHeader groups={groups} />
        {GROUP_ORDER.filter((k) => k !== "done" && groups[k].length > 0).map(
          (k) => (
            <TgSection
              key={k}
              title={GROUP_LABELS[k]}
              trailing={
                <span className="text-[11px] tabular-nums text-muted-foreground">
                  {groups[k].length}
                </span>
              }
            >
              <div className="divide-y divide-white/[0.04]">
                {groups[k].map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    tone={GROUP_TONE[k]}
                    onOpen={setSelected}
                  />
                ))}
              </div>
            </TgSection>
          ),
        )}
        {groups.done.length > 0 && (
          <DoneSection tasks={groups.done} onOpen={setSelected} />
        )}
      </div>
      <TgTaskSheet task={selected} onClose={() => setSelected(null)} />
    </>
  );
}
