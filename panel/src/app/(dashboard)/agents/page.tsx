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
import { AgentsFleetView } from "@/components/agents/agents-fleet-view";
import { A2AView } from "@/components/a2a/a2a-view";

// ---------------------------------------------------------------------------
// Valid tab values
// ---------------------------------------------------------------------------

interface TabDef {
  value: "fleet" | "conversations";
  label: string;
  hint: string;
}

const TAB_DEFS: TabDef[] = [
  {
    value: "fleet",
    label: "Fleet",
    hint: "Every agent's live state, spawn controls, and activity stream",
  },
  {
    value: "conversations",
    label: "Conversations",
    hint: "Live agent-to-agent message switchboard and history",
  },
];

const TAB_VALUES = TAB_DEFS.map((t) => t.value);
type TabValue = (typeof TAB_VALUES)[number];

function isValidTab(value: string | null): value is TabValue {
  return TAB_VALUES.includes(value as TabValue);
}

// ---------------------------------------------------------------------------
// Inner component that reads URL params
// ---------------------------------------------------------------------------

function AgentsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab");
  const activeTab: TabValue = isValidTab(rawTab) ? rawTab : "fleet";

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", value);
    router.replace(`/agents?${params.toString()}`);
  };

  return (
    <Tabs value={activeTab} onValueChange={handleTabChange}>
      <TabsList>
        {TAB_DEFS.map((tab) => (
          <Tooltip key={tab.value}>
            <TooltipTrigger asChild>
              {/* TooltipTrigger's asChild Slot merge clobbers TabsTrigger's
                  own data-state; re-assert the real selection state
                  explicitly (see task-detail/task-tabs.tsx) so the
                  data-[state=active] styling still fires. */}
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

      <TabsContent value="fleet" className="mt-4">
        <AgentsFleetView />
      </TabsContent>

      <TabsContent value="conversations" className="mt-4">
        <A2AView />
      </TabsContent>
    </Tabs>
  );
}

// ---------------------------------------------------------------------------
// Page export — wraps in Suspense for useSearchParams
// ---------------------------------------------------------------------------

export default function AgentsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <Skeleton className="h-9 w-72" />
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <AgentsPageContent />
    </Suspense>
  );
}
