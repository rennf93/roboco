"use client";

import { Suspense, useCallback, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAgents } from "@/hooks/use-agents";
import { JournalEntryType } from "@/types";
import { AgentList } from "@/components/journals/agent-list";
import { JournalView } from "@/components/journals/journal-view";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { BookOpen, Search, RefreshCw } from "lucide-react";

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

function JournalsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read state from URL params
  const urlAgentId = searchParams.get("agent");
  const urlSearch = searchParams.get("q") || "";
  const urlType = searchParams.get("type");
  const urlTask = searchParams.get("task");

  // Restore from localStorage if URL has no params (fresh navigation)
  useEffect(() => {
    const hasUrlParams = urlAgentId || urlSearch || urlType || urlTask;
    if (!hasUrlParams) {
      const saved = loadJournalsState();
      if (saved?.agent) {
        const params = new URLSearchParams();
        if (saved.agent) params.set("agent", saved.agent);
        if (saved.q) params.set("q", saved.q);
        if (saved.type) params.set("type", saved.type);
        if (saved.task) params.set("task", saved.task);
        const query = params.toString();
        if (query) {
          router.replace(`/journals?${query}`);
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Intentionally only run on mount

  // Derive state from URL
  const selectedAgentId = urlAgentId;
  const agentSearch = urlSearch;
  const typeFilter = (urlType as JournalEntryType) || "all";
  const taskFilter = urlTask;

  const { data: agents, isLoading: loadingAgents, refetch } = useAgents();

  // Save state to localStorage whenever URL params change
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

  // Update URL params
  const updateParams = useCallback((updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString());
    Object.entries(updates).forEach(([key, value]) => {
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
    });
    const query = params.toString();
    router.push(query ? `/journals?${query}` : "/journals");
  }, [router, searchParams]);

  const handleSelectAgent = useCallback((agentId: string | null) => {
    // Only reset filters when changing to a different agent
    if (agentId !== selectedAgentId) {
      updateParams({ agent: agentId, type: null, task: null });
    }
  }, [updateParams, selectedAgentId]);

  const handleAgentSearch = useCallback((value: string) => {
    updateParams({ q: value || null });
  }, [updateParams]);

  const handleTypeChange = useCallback((value: JournalEntryType | "all") => {
    updateParams({ type: value === "all" ? null : value });
  }, [updateParams]);

  const handleTaskChange = useCallback((value: string | null) => {
    updateParams({ task: value });
  }, [updateParams]);

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
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Agent Journals</h1>
          <p className="text-muted-foreground">
            View agent reflections, learnings, and decisions
          </p>
        </div>
        <Button variant="outline" onClick={() => refetch()}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-12 gap-6">
        {/* Sidebar */}
        <div className="col-span-12 lg:col-span-3">
          <Card>
            <CardContent className="p-3">
              {/* Agent Search */}
              <div className="relative mb-3">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  value={agentSearch}
                  onChange={(e) => handleAgentSearch(e.target.value)}
                  placeholder="Search agents..."
                  className="pl-9"
                />
              </div>

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
        <div className="col-span-12 lg:col-span-9">
          <Card>
            <CardContent className="p-6">
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
export default function JournalsPage() {
  return (
    <Suspense fallback={
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
    }>
      <JournalsPageContent />
    </Suspense>
  );
}
