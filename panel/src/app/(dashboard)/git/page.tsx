"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Skeleton } from "@/components/ui/skeleton";
import { GitBrowser } from "@/components/git";
import { WorkSessionsView } from "@/components/work-sessions";

interface TabDef {
  value: "repository" | "sessions";
  label: string;
  hint: string;
}

const TAB_DEFS: TabDef[] = [
  {
    value: "repository",
    label: "Repository",
    hint: "Browse status, branches, log, and diffs; run git actions",
  },
  {
    value: "sessions",
    label: "Work Sessions",
    hint: "Active agent work sessions — branch, commits, and PR per task",
  },
];

const TAB_VALUES = TAB_DEFS.map((t) => t.value);
type TabValue = (typeof TAB_VALUES)[number];

function isValidTab(value: string | null): value is TabValue {
  return TAB_VALUES.includes(value as TabValue);
}

function GitPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab");
  const activeTab: TabValue = isValidTab(rawTab) ? rawTab : "repository";

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", value);
    router.replace(`/git?${params.toString()}`);
  };

  return (
    <Tabs value={activeTab} onValueChange={handleTabChange}>
      <TabsList>
        {TAB_DEFS.map((tab) => (
          <Tooltip key={tab.value}>
            <TooltipTrigger asChild>
              {/* TooltipTrigger's asChild Slot merge clobbers TabsTrigger's
                  own data-state; re-stamp it so the active style survives. */}
              <TabsTrigger
                value={tab.value}
                data-state={tab.value === activeTab ? "active" : "inactive"}
              >
                {tab.label}
              </TabsTrigger>
            </TooltipTrigger>
            <TooltipContent>{tab.hint}</TooltipContent>
          </Tooltip>
        ))}
      </TabsList>

      <TabsContent value="repository" className="mt-4">
        <GitBrowser />
      </TabsContent>

      <TabsContent value="sessions" className="mt-4">
        <WorkSessionsView />
      </TabsContent>
    </Tabs>
  );
}

// Wrap in Suspense for useSearchParams
export default function GitPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <GitPageContent />
    </Suspense>
  );
}
