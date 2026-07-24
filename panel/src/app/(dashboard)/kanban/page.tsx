"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";

// The kanban views (dev/qa/pr-review/pm) now live as the Tasks page's Kanban
// tab (see (dashboard)/tasks/page.tsx). This route only exists so old links
// and bookmarks to /kanban and /kanban?view=qa keep working — it forwards
// straight to /tasks?tab=kanban(&view=X) and never renders any board itself.
function KanbanRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const view = searchParams.get("view");

  useEffect(() => {
    // replace (not push): the redirect itself shouldn't become a history
    // entry a user has to hit "back" through.
    router.replace(view ? `/tasks?tab=kanban&view=${view}` : "/tasks?tab=kanban");
  }, [router, view]);

  return (
    <div className="space-y-6">
      <Skeleton className="h-10 w-72" />
      <div className="grid grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-96 w-full" />
        ))}
      </div>
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function KanbanPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" />}>
      <KanbanRedirect />
    </Suspense>
  );
}
