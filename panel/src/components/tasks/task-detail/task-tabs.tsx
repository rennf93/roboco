"use client";

import { Task } from "@/types";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { TabOverview } from "./tab-overview";
import { TabPlan } from "./tab-plan";
import { TabProgress } from "./tab-progress";
import { TabCommits } from "./tab-commits";
import { TabNotes } from "./tab-notes";
import { TabDependencies } from "./tab-dependencies";
import { TabSessions } from "./tab-sessions";
import {
  FileText,
  Layout,
  Clock,
  GitCommit,
  StickyNote,
  Link2,
  MessageSquare,
} from "lucide-react";

interface TaskTabsProps {
  task: Task;
}

export function TaskTabs({ task }: TaskTabsProps) {
  // Calculate badge counts
  const progressCount = task.progress_updates.length + task.checkpoints.length;
  const commitCount = task.commits.length;
  const notesCount =
    (task.dev_notes ? 1 : 0) +
    (task.qa_notes ? 1 : 0) +
    (task.auditor_notes ? 1 : 0) +
    (task.quick_context ? 1 : 0);
  const depsCount = task.dependency_ids.length + task.blocker_ids.length;
  const sessionsCount = task.sessions?.length || 0;

  return (
    <Tabs defaultValue="overview" className="mt-6">
      <TabsList className="grid w-full grid-cols-7 lg:w-auto lg:inline-grid">
        <TabsTrigger value="overview" className="gap-2">
          <FileText className="h-4 w-4" />
          <span className="hidden sm:inline">Overview</span>
        </TabsTrigger>
        <TabsTrigger value="plan" className="gap-2">
          <Layout className="h-4 w-4" />
          <span className="hidden sm:inline">Plan</span>
          {task.plan && (
            <Badge variant="secondary" className="ml-1 h-5 px-1.5">
              {task.plan.sub_tasks.length}
            </Badge>
          )}
        </TabsTrigger>
        <TabsTrigger value="progress" className="gap-2">
          <Clock className="h-4 w-4" />
          <span className="hidden sm:inline">Progress</span>
          {progressCount > 0 && (
            <Badge variant="secondary" className="ml-1 h-5 px-1.5">
              {progressCount}
            </Badge>
          )}
        </TabsTrigger>
        <TabsTrigger value="sessions" className="gap-2">
          <MessageSquare className="h-4 w-4" />
          <span className="hidden sm:inline">Sessions</span>
          {sessionsCount > 0 && (
            <Badge variant="secondary" className="ml-1 h-5 px-1.5">
              {sessionsCount}
            </Badge>
          )}
        </TabsTrigger>
        <TabsTrigger value="commits" className="gap-2">
          <GitCommit className="h-4 w-4" />
          <span className="hidden sm:inline">Commits</span>
          {commitCount > 0 && (
            <Badge variant="secondary" className="ml-1 h-5 px-1.5">
              {commitCount}
            </Badge>
          )}
        </TabsTrigger>
        <TabsTrigger value="notes" className="gap-2">
          <StickyNote className="h-4 w-4" />
          <span className="hidden sm:inline">Notes</span>
          {notesCount > 0 && (
            <Badge variant="secondary" className="ml-1 h-5 px-1.5">
              {notesCount}
            </Badge>
          )}
        </TabsTrigger>
        <TabsTrigger value="dependencies" className="gap-2">
          <Link2 className="h-4 w-4" />
          <span className="hidden sm:inline">Deps</span>
          {depsCount > 0 && (
            <Badge variant="secondary" className="ml-1 h-5 px-1.5">
              {depsCount}
            </Badge>
          )}
        </TabsTrigger>
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
        <TabsContent value="sessions">
          <TabSessions task={task} />
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
      </div>
    </Tabs>
  );
}
