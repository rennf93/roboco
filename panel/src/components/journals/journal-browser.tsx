"use client";

import { useState } from "react";
import { useAgents } from "@/hooks/use-agents";
import { AgentList } from "./agent-list";
import { JournalView } from "./journal-view";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { BookOpen, Search, RefreshCw } from "lucide-react";

export function JournalBrowser() {
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [agentSearch, setAgentSearch] = useState("");
  const { data: agents, isLoading: loadingAgents, refetch } = useAgents();

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
                  onChange={(e) => setAgentSearch(e.target.value)}
                  placeholder="Search agents..."
                  className="pl-9"
                />
              </div>

              {/* Agent List */}
              <AgentList
                agents={filteredAgents}
                isLoading={loadingAgents}
                selectedAgentId={selectedAgentId}
                onSelectAgent={setSelectedAgentId}
              />
            </CardContent>
          </Card>
        </div>

        {/* Journal Content */}
        <div className="col-span-12 lg:col-span-9">
          <Card>
            <CardContent className="p-6">
              {selectedAgent ? (
                <JournalView agent={selectedAgent} />
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
