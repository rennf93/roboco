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
import { ProductsView } from "@/components/products/products-view";
import { ProjectsView } from "@/components/projects/projects-view";

// ---------------------------------------------------------------------------
// Valid tab values
// ---------------------------------------------------------------------------

interface TabDef {
  value: "products" | "projects";
  label: string;
  hint: string;
}

const TAB_DEFS: TabDef[] = [
  {
    value: "products",
    label: "Products",
    hint: "Products the fleet ships against",
  },
  {
    value: "projects",
    label: "Projects",
    hint: "Manage repos, git tokens, and per-project settings",
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

function WorkstationPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab");
  const activeTab: TabValue = isValidTab(rawTab) ? rawTab : "products";

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", value);
    router.replace(`/workstation?${params.toString()}`);
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

      <TabsContent value="products" className="mt-4">
        <ProductsView />
      </TabsContent>

      <TabsContent value="projects" className="mt-4">
        <ProjectsView />
      </TabsContent>
    </Tabs>
  );
}

// ---------------------------------------------------------------------------
// Page export — wraps in Suspense for useSearchParams
// ---------------------------------------------------------------------------

export default function WorkstationPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <Skeleton className="h-9 w-72" />
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <WorkstationPageContent />
    </Suspense>
  );
}
