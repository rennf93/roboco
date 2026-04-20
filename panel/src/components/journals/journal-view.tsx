"use client";

import { useState, useMemo } from "react";
import { JournalEntryType, Agent } from "@/types";
import { useJournalByAgent, useAgentJournalEntries, useMyGrowthMetrics } from "@/hooks/use-journals";
import { useTasks } from "@/hooks/use-tasks";
import { GrowthSummary } from "./growth-summary";
import { EntryCard } from "./entry-card";
import { EntryFilter } from "./entry-filter";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { BookOpen, User } from "lucide-react";
import { getAgentDisplayName } from "@/lib/agent-utils";

interface JournalViewProps {
  agent: Agent;
  typeFilter?: JournalEntryType | "all";
  onTypeChange?: (type: JournalEntryType | "all") => void;
  taskFilter?: string | null;
  onTaskChange?: (taskId: string | null) => void;
}

export function JournalView({
  agent,
  typeFilter: externalTypeFilter,
  onTypeChange,
  taskFilter: externalTaskFilter,
  onTaskChange,
}: JournalViewProps) {
  // Use internal state if no external control provided
  const [internalTypeFilter, setInternalTypeFilter] = useState<JournalEntryType | "all">("all");
  const [internalTaskFilter, setInternalTaskFilter] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  // Use external or internal state
  const typeFilter = externalTypeFilter ?? internalTypeFilter;
  const taskFilter = externalTaskFilter ?? internalTaskFilter;
  const handleTypeChange = onTypeChange ?? setInternalTypeFilter;
  const handleTaskChange = onTaskChange ?? setInternalTaskFilter;

  // Fetch journal and entries for the selected agent using their slug
  const { data: journal, isLoading: loadingJournal } = useJournalByAgent(agent.agent_id);

  // Fetch ALL entries (without task filter) to build the task dropdown options
  const { data: allEntries } = useAgentJournalEntries(
    agent.agent_id,
    typeFilter !== "all" ? { entry_type: typeFilter } : {}
  );

  // Fetch filtered entries for display
  const { data: entries, isLoading: loadingEntries } = useAgentJournalEntries(
    agent.agent_id,
    {
      ...(typeFilter !== "all" ? { entry_type: typeFilter } : {}),
      ...(taskFilter ? { task_id: taskFilter } : {}),
    }
  );
  const { data: growth, isLoading: loadingGrowth } = useMyGrowthMetrics();
  const { data: allTasks, isLoading: loadingTasks } = useTasks();

  // Extract unique task_ids from entries and get those tasks for the dropdown
  // This shows only tasks that the agent actually has journal entries about
  const tasks = useMemo(() => {
    const entryTaskIds = new Set(
      (allEntries ?? [])
        .filter((e) => e.task_id)
        .map((e) => e.task_id!)
    );
    return (allTasks ?? []).filter((task) => entryTaskIds.has(task.id));
  }, [allEntries, allTasks]);

  // Filter entries by search query
  const filteredEntries = (entries ?? []).filter((entry) => {
    if (!searchQuery) return true;
    const query = searchQuery.toLowerCase();
    return (
      entry.title.toLowerCase().includes(query) ||
      entry.content.toLowerCase().includes(query) ||
      entry.tags.some((tag) => tag.toLowerCase().includes(query))
    );
  });

  return (
    <div className="space-y-6">
      {/* Agent Header */}
      <div className="flex items-center gap-4 pb-4 border-b">
        <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center">
          <User className="h-6 w-6 text-muted-foreground" />
        </div>
        <div>
          <h2 className="text-xl font-semibold">{getAgentDisplayName(agent.agent_id)}</h2>
          <p className="text-sm text-muted-foreground capitalize">
            {agent.role.replace(/_/g, " ")} - {agent.team?.replace(/_/g, " ") || "N/A"}
          </p>
        </div>
      </div>

      {/* Growth Summary */}
      <GrowthSummary
        journal={journal}
        growth={growth}
        isLoading={loadingJournal || loadingGrowth}
      />

      {/* Entries Section */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <BookOpen className="h-5 w-5" />
            Journal Entries
          </h3>
        </div>

        {/* Filter */}
        <div className="mb-4">
          <EntryFilter
            typeFilter={typeFilter}
            onTypeChange={handleTypeChange}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            taskFilter={taskFilter}
            onTaskChange={handleTaskChange}
            tasks={tasks ?? []}
            tasksLoading={loadingTasks}
          />
        </div>

        {/* Entries List */}
        {loadingEntries ? (
          <div className="space-y-4">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-32" />
            ))}
          </div>
        ) : filteredEntries.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            <BookOpen className="h-12 w-12 mx-auto mb-4 opacity-50" />
            <p>No journal entries found</p>
            <p className="text-sm mt-1">
              {searchQuery
                ? "Try a different search term"
                : "Entries will appear as the agent works"}
            </p>
          </div>
        ) : (
          <ScrollArea className="h-[500px] pr-4">
            <div className="space-y-4">
              {filteredEntries.map((entry) => (
                <EntryCard key={entry.id} entry={entry} />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>
    </div>
  );
}
