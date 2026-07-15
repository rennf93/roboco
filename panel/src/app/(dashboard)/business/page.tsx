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
import { GoalsTab } from "@/components/business/goals-tab";
import { CompanyScorecardCard } from "@/components/business/company-scorecard-card";
import { SecretaryTab } from "@/components/business/secretary-tab";
import { PitchesTab } from "@/components/business/pitches-tab";

// ---------------------------------------------------------------------------
// Valid tab values
// ---------------------------------------------------------------------------

interface TabDef {
  value: "goals" | "scorecard" | "secretary" | "pitches";
  label: string;
  hint: string;
}

const TAB_DEFS: TabDef[] = [
  {
    value: "goals",
    label: "Goals",
    hint: "CEO-owned charter — north star, brand voice, objectives, constraints",
  },
  {
    value: "scorecard",
    label: "Scorecard",
    hint: "Live delivery, spend, and speed metrics against the charter",
  },
  {
    value: "secretary",
    label: "Secretary",
    hint: "Chat with your chief-of-staff and confirm or reject pending directives",
  },
  {
    value: "pitches",
    label: "Pitches",
    hint: "Board-authored product pitches awaiting your decision",
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

function BusinessPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab");
  const activeTab: TabValue = isValidTab(rawTab) ? rawTab : "goals";

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", value);
    router.replace(`/business?${params.toString()}`);
  };

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Business</h1>
        <p className="text-muted-foreground">
          Company goals, your chief-of-staff Secretary, and Board pitches — all
          in one place.
        </p>
      </div>

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

        <TabsContent value="goals" className="mt-4">
          <GoalsTab />
        </TabsContent>

        <TabsContent value="scorecard" className="mt-4">
          <CompanyScorecardCard />
        </TabsContent>

        <TabsContent value="secretary" className="mt-4">
          <SecretaryTab />
        </TabsContent>

        <TabsContent value="pitches" className="mt-4">
          <PitchesTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page export — wraps in Suspense for useSearchParams
// ---------------------------------------------------------------------------

export default function BusinessPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <div>
            <Skeleton className="h-9 w-32 mb-2" />
            <Skeleton className="h-5 w-96" />
          </div>
          <Skeleton className="h-9 w-72" />
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <BusinessPageContent />
    </Suspense>
  );
}
