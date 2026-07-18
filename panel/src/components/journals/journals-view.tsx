"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAgents } from "@/hooks/use-agents";
import { JournalEntryType } from "@/types";
import { AgentList } from "@/components/journals/agent-list";
import { JournalView } from "@/components/journals/journal-view";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { usePageRefresh } from "@/hooks";
import { BookOpen, Search } from "lucide-react";

const JOURNALS_STATE_KEY = "roboco-journals-state";

interface JournalsState {
  agent: string | null;
  q: string | null;
  type: string | null;
  task: string | null;
}

function saveJournalsState(state: JournalsState) {
  try {
    localStorage.setItem(JOURNALS_STATE_KEY, JSON.stringify(state));
  } catch {
    // Ignore localStorage errors
  }
}

function loadJournalsState(): JournalsState | null {
  try {
    const stored = localStorage.getItem(JOURNALS_STATE_KEY);
    return stored ? JSON.parse(stored) : null;
  } catch {
    return null;
  }
}

/** Journals tab content — extracted from the standalone /journals page so it
 * can live inside the Agents hub tab shell (see agents/page.tsx). Its
 * `agent`/`type`/`task` params keep working on the /agents route; every
 * writer below targets /agents (not /journals) preserving the rest of the
 * query string (e.g. `tab=journals`), copying the A2AView idiom. The agent
 * search box is local state, not a URL param — a per-keystroke `router.push`
 * would bounce the scroll position on every character. */
function JournalsViewContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read state from URL params
  const urlAgentId = searchParams.get("agent");
  const urlType = searchParams.get("type");
  const urlTask = searchParams.get("task");

  // Local-only: not URL-synced (see file header), lazily seeded from the last
  // saved search so a fresh /agents?tab=journals visit isn't blank.
  const [agentSearch, setAgentSearch] = useState(
    () => loadJournalsState()?.q ?? "",
  );

  // Restore from localStorage if URL has no params (fresh navigation)
  useEffect(() => {
    const hasUrlParams = urlAgentId || urlType || urlTask;
    if (!hasUrlParams) {
      const saved = loadJournalsState();
      if (saved?.agent) {
        const params = new URLSearchParams(searchParams.toString());
        params.set("agent", saved.agent);
        if (saved.type) params.set("type", saved.type);
        if (saved.task) params.set("task", saved.task);
        router.replace(`/agents?${params.toString()}`);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Intentionally only run on mount

  // Derive state from URL
  const selectedAgentId = urlAgentId;
  const typeFilter = (urlType as JournalEntryType) || "all";
  const taskFilter = urlTask;

  const { data: agents, isLoading: loadingAgents, refetch } = useAgents();

  const { register, unregister } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  // Save state to localStorage whenever the URL params or search change
  useEffect(() => {
    if (selectedAgentId) {
      saveJournalsState({
        agent: selectedAgentId,
        q: agentSearch || null,
        type: urlType, // Use raw URL param (null if "all")
        task: taskFilter,
      });
    }
  }, [selectedAgentId, agentSearch, urlType, taskFilter]);

  // Update URL params — copies the current query string first so `tab` (and
  // any other param) survives the round trip to /agents.
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
      router.push(query ? `/agents?${query}` : "/agents");
    },
    [router, searchParams],
  );

  const handleSelectAgent = useCallback(
    (agentId: string | null) => {
      // Only reset filters when changing to a different agent
      if (agentId !== selectedAgentId) {
        updateParams({ agent: agentId, type: null, task: null });
      }
    },
    [updateParams, selectedAgentId],
  );

  const handleTypeChange = useCallback(
    (value: JournalEntryType | "all") => {
      updateParams({ type: value === "all" ? null : value });
    },
    [updateParams],
  );

  const handleTaskChange = useCallback(
    (value: string | null) => {
      updateParams({ task: value });
    },
    [updateParams],
  );

  // Filter agents by search
  const filteredAgents = (agents ?? []).filter((agent) => {
    if (!agentSearch) return true;
    const query = agentSearch.toLowerCase();
    return (
      agent.agent_id.toLowerCase().includes(query) ||
      agent.role.toLowerCase().includes(query) ||
      agent.team?.toLowerCase().includes(query)
    );
  });

  // Get selected agent
  const selectedAgent = agents?.find((a) => a.agent_id === selectedAgentId);

  return (
    // h-[calc(100dvh-7rem)]: the tab shell's TabsList sits above this content
    // (unlike the old standalone /journals page), so a plain h-full has no
    // definite height to fill — same fixed-height idiom as A2AView.
    <div className="flex h-[calc(100dvh-7rem)] flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Agent Journals</h1>
          <p className="text-muted-foreground">
            View agent reflections, learnings, and decisions
          </p>
        </div>
      </div>

      {/* Main Content — one screen; the agent list and the detail each scroll inside */}
      <div className="grid grid-cols-12 gap-6 flex-1 min-h-0">
        {/* Sidebar */}
        <div className="col-span-12 lg:col-span-3 min-h-0">
          <Card className="h-full flex flex-col">
            <CardContent className="p-3 flex flex-1 flex-col min-h-0">
              {/* Agent Search */}
              <HelpTip label="Filters the agent list below by ID, role, or team">
                <div className="relative mb-3">
                  <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    value={agentSearch}
                    onChange={(e) => setAgentSearch(e.target.value)}
                    placeholder="Search agents..."
                    className="pl-9"
                  />
                </div>
              </HelpTip>

              {/* Agent List */}
              <AgentList
                agents={filteredAgents}
                isLoading={loadingAgents}
                selectedAgentId={selectedAgentId}
                onSelectAgent={handleSelectAgent}
              />
            </CardContent>
          </Card>
        </div>

        {/* Journal Content */}
        <div className="col-span-12 lg:col-span-9 min-h-0">
          <Card className="h-full flex flex-col">
            <CardContent className="p-6 flex-1 min-h-0 overflow-hidden">
              {selectedAgent ? (
                <JournalView
                  agent={selectedAgent}
                  typeFilter={typeFilter as JournalEntryType | "all"}
                  onTypeChange={handleTypeChange}
                  taskFilter={taskFilter}
                  onTaskChange={handleTaskChange}
                />
              ) : (
                <div className="text-center py-16 text-muted-foreground">
                  <BookOpen className="h-16 w-16 mx-auto mb-4 opacity-50" />
                  <h3 className="text-lg font-medium mb-2">Select an Agent</h3>
                  <p className="text-sm">
                    Choose an agent from the list to view their journal entries
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export function JournalsView() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <Skeleton className="h-9 w-48 mb-2" />
              <Skeleton className="h-5 w-64" />
            </div>
          </div>
          <div className="grid grid-cols-12 gap-6">
            <div className="col-span-12 lg:col-span-3">
              <Card>
                <CardContent className="p-3 space-y-2">
                  <Skeleton className="h-10 w-full" />
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-12 w-full" />
                  ))}
                </CardContent>
              </Card>
            </div>
            <div className="col-span-12 lg:col-span-9">
              <Card>
                <CardContent className="p-6">
                  <Skeleton className="h-64 w-full" />
                </CardContent>
              </Card>
            </div>
          </div>
        </div>
      }
    >
      <JournalsViewContent />
    </Suspense>
  );
}
