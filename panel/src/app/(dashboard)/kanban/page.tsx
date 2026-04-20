"use client";

import { Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { DevKanban, QaKanban, PmKanban } from "@/components/kanban";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Code, TestTube, ClipboardList } from "lucide-react";

type KanbanView = "dev" | "qa" | "pm";

function KanbanPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read view from URL params, default to "dev"
  const view = (searchParams.get("view") as KanbanView) || "dev";

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
          <TabsTrigger value="dev" className="gap-2">
            <Code className="h-4 w-4" />
            Developer
          </TabsTrigger>
          <TabsTrigger value="qa" className="gap-2">
            <TestTube className="h-4 w-4" />
            QA
          </TabsTrigger>
          <TabsTrigger value="pm" className="gap-2">
            <ClipboardList className="h-4 w-4" />
            PM
          </TabsTrigger>
        </TabsList>

        <TabsContent value="dev" className="mt-6">
          <DevKanban />
        </TabsContent>
        <TabsContent value="qa" className="mt-6">
          <QaKanban />
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
    <Suspense fallback={
      <div className="space-y-6">
        <Skeleton className="h-10 w-72" />
        <div className="grid grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-96 w-full" />
          ))}
        </div>
      </div>
    }>
      <KanbanPageContent />
    </Suspense>
  );
}
