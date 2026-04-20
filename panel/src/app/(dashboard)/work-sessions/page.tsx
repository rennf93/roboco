"use client";

import { Suspense, useMemo, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useWorkSessions } from "@/hooks/use-work-sessions";
import { WorkSessionStatus } from "@/types";
import { OfflineState } from "@/components/ui/offline-state";
import { WorkSessionTable, WorkSessionFilters } from "@/components/work-sessions";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RefreshCw } from "lucide-react";

function WorkSessionsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read state from URL params
  const searchQuery = searchParams.get("q") || "";
  const statusParam = searchParams.get("status");
  const statusFilter = useMemo(
    () => (statusParam?.split(",").filter(Boolean) as WorkSessionStatus[]) || [],
    [statusParam]
  );

  // Update URL params
  const updateParams = useCallback(
    (updates: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString());
      Object.entries(updates).forEach(([key, value]) => {
        if (value) {
          params.set(key, value);
        } else {
          params.delete(key);
        }
      });
      const query = params.toString();
      router.push(query ? `/work-sessions?${query}` : "/work-sessions");
    },
    [router, searchParams]
  );

  const handleSearchChange = useCallback(
    (value: string) => {
      updateParams({ q: value || null });
    },
    [updateParams]
  );

  const handleStatusChange = useCallback(
    (value: WorkSessionStatus[]) => {
      updateParams({ status: value.length > 0 ? value.join(",") : null });
    },
    [updateParams]
  );

  // Fetch work sessions
  const { data: sessions, isLoading, error, refetch } = useWorkSessions();

  // Filter sessions client-side for search and multi-select status filter
  const filteredSessions = useMemo(() => {
    if (!sessions) return [];

    return sessions.filter((session) => {
      // Search filter - match branch name
      if (
        searchQuery &&
        !session.branch_name.toLowerCase().includes(searchQuery.toLowerCase())
      ) {
        return false;
      }

      // Status filter (if any selected, session must match one of them)
      if (statusFilter.length > 0 && !statusFilter.includes(session.status)) {
        return false;
      }

      return true;
    });
  }, [sessions, searchQuery, statusFilter]);

  // Check if it's a connection error (backend not running)
  const isOffline =
    error &&
    (error.message?.includes("Network Error") ||
      error.message?.includes("ECONNREFUSED") ||
      (error as { code?: string })?.code === "ERR_NETWORK");

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Work Sessions</h1>
          <p className="text-muted-foreground">
            Track git branches and pull requests for active work
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filters - Sticky */}
      <div className="sticky top-0 z-10 -mx-6 px-6 py-2 bg-muted/30 backdrop-blur-sm">
        <WorkSessionFilters
          searchQuery={searchQuery}
          onSearchChange={handleSearchChange}
          statusFilter={statusFilter}
          onStatusChange={handleStatusChange}
        />
      </div>

      {/* Content */}
      {isOffline ? (
        <OfflineState
          title="Cannot Load Work Sessions"
          description="Start the RoboCo orchestrator to view work sessions. Work sessions track agent activity on git branches."
          onRetry={() => refetch()}
        />
      ) : (
        <WorkSessionTable sessions={filteredSessions} isLoading={isLoading} />
      )}
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function WorkSessionsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <Skeleton className="h-9 w-48 mb-2" />
              <Skeleton className="h-5 w-72" />
            </div>
          </div>
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <WorkSessionsPageContent />
    </Suspense>
  );
}
