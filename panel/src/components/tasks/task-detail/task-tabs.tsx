"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Task } from "@/types";
import { useTaskFindings } from "@/hooks/use-tasks";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { TabOverview } from "./tab-overview";
import { TabPlan } from "./tab-plan";
import { TabProgress } from "./tab-progress";
import { TabCommits } from "./tab-commits";
import { TabNotes } from "./tab-notes";
import { TabDependencies } from "./tab-dependencies";
import { TabFindings } from "./tab-findings";
import { TabCollision } from "./tab-collision";
import {
  FileText,
  Layout,
  Clock,
  GitCommit,
  StickyNote,
  Link2,
  ListChecks,
  GitBranch,
  type LucideIcon,
} from "lucide-react";

interface TaskTabsProps {
  task: Task;
}

interface TabDef {
  value: string;
  label: string;
  icon: LucideIcon;
  hint: string;
  count?: number;
}

const DEFAULT_TAB = "overview";

export function TaskTabs({ task }: TaskTabsProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Calculate badge counts
  const progressCount = task.progress_updates.length + task.checkpoints.length;
  const commitCount = task.commits.length;
  const notesCount =
    (task.dev_notes ? 1 : 0) +
    (task.qa_notes ? 1 : 0) +
    (task.auditor_notes ? 1 : 0) +
    (task.quick_context ? 1 : 0);
  const depsCount = task.dependency_ids.length + task.blocker_ids.length;
  const { data: findingsData } = useTaskFindings(task.id);
  const findingsCount = findingsData?.total ?? 0;

  const tabs: TabDef[] = [
    {
      value: "overview",
      label: "Overview",
      icon: FileText,
      hint: "Description, acceptance criteria, and metadata",
    },
    {
      value: "plan",
      label: "Plan",
      icon: Layout,
      hint: "The delegated sub-task plan",
      count: task.plan ? task.plan.sub_tasks.length : undefined,
    },
    {
      value: "progress",
      label: "Progress",
      icon: Clock,
      hint: "Progress updates and checkpoints",
      count: progressCount > 0 ? progressCount : undefined,
    },
    {
      value: "commits",
      label: "Commits",
      icon: GitCommit,
      hint: "Commits linked to this task",
      count: commitCount > 0 ? commitCount : undefined,
    },
    {
      value: "notes",
      label: "Notes",
      icon: StickyNote,
      hint: "Dev, QA, and auditor notes",
      count: notesCount > 0 ? notesCount : undefined,
    },
    {
      value: "dependencies",
      label: "Deps",
      icon: Link2,
      hint: "Dependencies and blockers",
      count: depsCount > 0 ? depsCount : undefined,
    },
    {
      value: "findings",
      label: "Findings",
      icon: ListChecks,
      hint: "Revision-findings ledger — QA / PR-review / PM / CEO bounce feedback",
      count: findingsCount > 0 ? findingsCount : undefined,
    },
    {
      value: "collision",
      label: "Collision",
      icon: GitBranch,
      hint: "Sibling collision surface + declared-vs-actual drift",
    },
  ];

  // The active tab lives in the URL (?tab=) so it survives reloads,
  // back/forward, and prev/next task navigation. Unknown values fall back to
  // the default rather than rendering an empty pane.
  const tabParam = searchParams.get("tab");
  const activeTab = tabs.some((t) => t.value === tabParam)
    ? (tabParam as string)
    : DEFAULT_TAB;

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value === DEFAULT_TAB) params.delete("tab");
    else params.set("tab", value);
    const qs = params.toString();
    router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
  };

  return (
    <Tabs value={activeTab} onValueChange={handleTabChange} className="mt-6">
      <TabsList className="grid w-full grid-cols-8 lg:w-auto lg:inline-grid">
        {tabs.map((tab) => (
          <Tooltip key={tab.value}>
            <TooltipTrigger asChild>
              <TabsTrigger value={tab.value} className="gap-2">
                <tab.icon className="h-4 w-4" />
                <span className="hidden sm:inline">{tab.label}</span>
                {tab.count !== undefined && (
                  <Badge variant="secondary" className="ml-1 h-5 px-1.5">
                    {tab.count}
                  </Badge>
                )}
              </TabsTrigger>
            </TooltipTrigger>
            <TooltipContent>{tab.hint}</TooltipContent>
          </Tooltip>
        ))}
      </TabsList>

      <div className="mt-4">
        <TabsContent value="overview">
          <TabOverview task={task} />
        </TabsContent>
        <TabsContent value="plan">
          <TabPlan task={task} />
        </TabsContent>
        <TabsContent value="progress">
          <TabProgress task={task} />
        </TabsContent>
        <TabsContent value="commits">
          <TabCommits task={task} />
        </TabsContent>
        <TabsContent value="notes">
          <TabNotes task={task} />
        </TabsContent>
        <TabsContent value="dependencies">
          <TabDependencies task={task} />
        </TabsContent>
        <TabsContent value="findings">
          <TabFindings task={task} />
        </TabsContent>
        <TabsContent value="collision">
          <TabCollision task={task} />
        </TabsContent>
      </div>
    </Tabs>
  );
}
