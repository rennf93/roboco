"use client";

import { Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  DevKanban,
  QaKanban,
  PrReviewKanban,
  PmKanban,
} from "@/components/kanban";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { pickTab } from "@/lib/tabs";
import { Code, TestTube, GitPullRequest, ClipboardList } from "lucide-react";

type KanbanView = "dev" | "qa" | "pr-review" | "pm";
const KANBAN_VIEWS = ["dev", "qa", "pr-review", "pm"] as const satisfies readonly KanbanView[];

function KanbanPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read view from URL params; fall back to "dev" on null/empty/invalid.
  const view: KanbanView = pickTab(searchParams.get("view"), KANBAN_VIEWS, "dev");

  const handleViewChange = (newView: string) => {
    if (newView === "dev") {
      router.push("/kanban");
    } else {
      router.push(`/kanban?view=${newView}`);
    }
  };

  return (
    <div className="space-y-6">
      <Tabs value={view} onValueChange={handleViewChange}>
        <TabsList>
          {/* TooltipTrigger's asChild Slot merge clobbers TabsTrigger's own
              data-state with the tooltip's — re-assert the real selection
              state explicitly so data-[state=active] styling survives
              (same fix as task-detail/task-tabs.tsx). */}
          <Tooltip>
            <TooltipTrigger asChild>
              <TabsTrigger
                value="dev"
                data-state={view === "dev" ? "active" : "inactive"}
                className="gap-2"
              >
                <Code className="h-4 w-4" />
                Developer
              </TabsTrigger>
            </TooltipTrigger>
            <TooltipContent>
              Tasks claimed and worked by developers — backlog through
              completion
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <TabsTrigger
                value="qa"
                data-state={view === "qa" ? "active" : "inactive"}
                className="gap-2"
              >
                <TestTube className="h-4 w-4" />
                QA
              </TabsTrigger>
            </TooltipTrigger>
            <TooltipContent>Quality assurance review workflow</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <TabsTrigger
                value="pr-review"
                data-state={view === "pr-review" ? "active" : "inactive"}
                className="gap-2"
              >
                <GitPullRequest className="h-4 w-4" />
                PR Review
              </TabsTrigger>
            </TooltipTrigger>
            <TooltipContent>
              In-path PR-review gate for assembled PRs, before the PM merges
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <TabsTrigger
                value="pm"
                data-state={view === "pm" ? "active" : "inactive"}
                className="gap-2"
              >
                <ClipboardList className="h-4 w-4" />
                PM
              </TabsTrigger>
            </TooltipTrigger>
            <TooltipContent>
              Project management overview — every lifecycle state, including
              recovery states
            </TooltipContent>
          </Tooltip>
        </TabsList>

        <TabsContent value="dev" className="mt-6">
          <DevKanban />
        </TabsContent>
        <TabsContent value="qa" className="mt-6">
          <QaKanban />
        </TabsContent>
        <TabsContent value="pr-review" className="mt-6">
          <PrReviewKanban />
        </TabsContent>
        <TabsContent value="pm" className="mt-6">
          <PmKanban />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function KanbanPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <Skeleton className="h-10 w-72" />
          <div className="grid grid-cols-4 gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-96 w-full" />
            ))}
          </div>
        </div>
      }
    >
      <KanbanPageContent />
    </Suspense>
  );
}
