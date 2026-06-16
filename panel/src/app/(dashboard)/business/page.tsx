"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { GoalsTab } from "@/components/business/goals-tab";
import { SecretaryTab } from "@/components/business/secretary-tab";
import { PitchesTab } from "@/components/business/pitches-tab";

// ---------------------------------------------------------------------------
// Valid tab values
// ---------------------------------------------------------------------------

const TAB_VALUES = ["goals", "secretary", "pitches"] as const;
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
          Company goals, your chief-of-staff Secretary, and Board pitches — all in
          one place.
        </p>
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="goals">Goals</TabsTrigger>
          <TabsTrigger value="secretary">Secretary</TabsTrigger>
          <TabsTrigger value="pitches">Pitches</TabsTrigger>
        </TabsList>

        <TabsContent value="goals" className="mt-4">
          <GoalsTab />
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
