"use client";

import { useState, useCallback, Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { KBIndexType, RAGQueryResponse } from "@/types";
import {
  useKBStats,
  useKBSearch,
  useRAGQuery,
  useRAGHealth,
  useDeleteIndex,
  useRefreshIndex,
  useReindexAll,
  useAskMentor,
} from "@/hooks/use-knowledge-base";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Progress } from "@/components/ui/progress";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  RefreshCw,
  Search,
  Sparkles,
  FolderTree,
  Settings,
  Trash2,
  CheckCircle,
  XCircle,
  Activity,
  HardDrive,
  FileText,
  Brain,
} from "lucide-react";
import { OfflineState } from "@/components/ui/offline-state";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";
import { getErrorMessage } from "@/lib/api/client";
import { pickTab } from "@/lib/tabs";
import { usePageRefresh } from "@/hooks";

// Components
import { KBSearchBar } from "./kb-search-bar";
import { KBFilters } from "./kb-filters";
import { KBStatsCard } from "./kb-stats-card";
import { KBResultList } from "./kb-result-list";
import { RAGQueryInput } from "./rag-query-input";
import { RAGAnswerDisplay } from "./rag-answer-display";
import { MentorChat } from "./mentor-chat";
import { KBCategoryNav } from "./kb-category-nav";
import { KBCategoryView } from "./kb-category-view";
import { getIndexTypeDescription } from "./kb-index-type-badge";

const TAB_VALUES = ["search", "ask", "mentor", "browse", "admin"] as const;
type TabValue = (typeof TAB_VALUES)[number];

const INDEX_LABELS: Record<KBIndexType, string> = {
  [KBIndexType.DOCUMENTATION]: "Documentation",
  [KBIndexType.CONVERSATIONS]: "Conversations",
  [KBIndexType.JOURNALS]: "Agent Journals",
  [KBIndexType.ERRORS]: "Error Solutions",
  [KBIndexType.STANDARDS]: "Standards",
  [KBIndexType.DECISIONS]: "Decisions",
  [KBIndexType.REVIEWS]: "Code Reviews",
  [KBIndexType.LEARNINGS]: "Learnings",
  [KBIndexType.PLAYBOOKS]: "Playbooks",
  [KBIndexType.VAULT_NOTES]: "Vault Notes",
};

// Valid KB index types for URL param validation
const VALID_INDEX_TYPES: KBIndexType[] = [
  KBIndexType.DOCUMENTATION,
  KBIndexType.CONVERSATIONS,
  KBIndexType.JOURNALS,
  KBIndexType.ERRORS,
  KBIndexType.STANDARDS,
  KBIndexType.DECISIONS,
  KBIndexType.REVIEWS,
  KBIndexType.LEARNINGS,
  KBIndexType.PLAYBOOKS,
  KBIndexType.VAULT_NOTES,
];

function KnowledgeBaseBrowserContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read state from URL params; fall back to "search" on null/empty/invalid.
  const activeTab: TabValue = pickTab(searchParams.get("tab"), TAB_VALUES, "search");
  const searchQuery = searchParams.get("q") || "";
  const filtersParam = searchParams.get("filters");
  const searchFilters: KBIndexType[] = filtersParam
    ? (filtersParam
        .split(",")
        .filter((f) =>
          VALID_INDEX_TYPES.includes(f as KBIndexType),
        ) as KBIndexType[])
    : [];
  const selectedCategory =
    (searchParams.get("category") as KBIndexType) || null;

  // RAG state (transient, not URL-persisted)
  const [ragQuestion, setRagQuestion] = useState<string | null>(null);
  const [ragResponse, setRagResponse] = useState<RAGQueryResponse | null>(null);
  const [ragError, setRagError] = useState<string | null>(null);

  // Update URL params helper
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
      router.push(query ? `/knowledge-base?${query}` : "/knowledge-base");
    },
    [router, searchParams],
  );

  // State update handlers
  const handleTabChange = useCallback(
    (tab: TabValue) => {
      updateParams({ tab: tab === "search" ? null : tab });
    },
    [updateParams],
  );

  const handleSearchChange = useCallback(
    (query: string) => {
      updateParams({ q: query || null });
    },
    [updateParams],
  );

  const handleFiltersChange = useCallback(
    (filters: KBIndexType[]) => {
      updateParams({ filters: filters.length > 0 ? filters.join(",") : null });
    },
    [updateParams],
  );

  const handleCategoryChange = useCallback(
    (category: KBIndexType | null) => {
      updateParams({ category });
    },
    [updateParams],
  );

  // Data hooks
  const {
    data: stats,
    isLoading: statsLoading,
    error: statsError,
    refetch: refetchStats,
  } = useKBStats();
  const { data: searchResults, isLoading: searchLoading } = useKBSearch({
    query: searchQuery,
    index_types: searchFilters.length > 0 ? searchFilters : undefined,
  });
  const ragMutation = useRAGQuery();

  // Admin hooks
  const {
    data: health,
    isLoading: loadingHealth,
    refetch: refetchHealth,
  } = useRAGHealth();

  const { register, unregister, refresh } = usePageRefresh();

  useEffect(() => {
    const callbacks = [
      () => {
        void refetchStats();
      },
      () => {
        void refetchHealth();
      },
    ];
    callbacks.forEach((cb) => register(cb));
    return () => {
      callbacks.forEach((cb) => unregister(cb));
    };
  }, [register, unregister, refetchStats, refetchHealth]);

  const deleteIndex = useDeleteIndex();
  const refreshIndex = useRefreshIndex();
  const reindexAll = useReindexAll();
  const askMentor = useAskMentor();

  // Handle RAG query
  const handleRAGQuery = async (question: string) => {
    setRagQuestion(question);
    setRagError(null);
    setRagResponse(null);
    try {
      const response = await ragMutation.mutateAsync({ question });
      setRagResponse(response);
    } catch (error) {
      console.error("RAG query failed:", error);
      const errorMessage = getErrorMessage(error);
      setRagError(errorMessage);
      toast.error(`RAG query failed: ${errorMessage}`);
    }
  };

  // Admin handlers
  const handleDeleteIndex = async (indexType: KBIndexType) => {
    try {
      await deleteIndex.mutateAsync(indexType);
      toast.success(`Deleted ${INDEX_LABELS[indexType]} index`);
    } catch (error) {
      toast.error(`Failed to delete index: ${getErrorMessage(error)}`);
    }
  };

  const handleRefreshIndex = async (indexType: KBIndexType) => {
    try {
      await refreshIndex.mutateAsync({ index_type: indexType, sources: [] });
      toast.success(`Refreshing ${INDEX_LABELS[indexType]} index`);
    } catch (error) {
      toast.error(`Failed to refresh index: ${getErrorMessage(error)}`);
    }
  };

  const handleReindexAll = async () => {
    try {
      const result = await reindexAll.mutateAsync({ force: true });
      // Show detailed results if available
      if (result.overall_success) {
        const docsCount = result.documentation?.successful ?? 0;
        const warns = result.warnings?.length
          ? ` Warnings: ${result.warnings.length}`
          : "";
        toast.success(`Reindexed ${docsCount} docs.${warns}`);
      } else {
        toast.warning(
          `Reindex completed with issues: ${result.warnings?.join(", ") ?? "Unknown errors"}`,
        );
      }
    } catch (error) {
      toast.error(`Failed to reindex: ${getErrorMessage(error)}`);
    }
  };

  // Mentor ask function for chat component
  const handleMentorAsk = async (question: string, conversationId?: string) => {
    try {
      return await askMentor.mutateAsync({
        question,
        conversation_id: conversationId,
      });
    } catch (error) {
      toast.error(`Mentor query failed: ${getErrorMessage(error)}`);
      throw error;
    }
  };

  // Calculate totals for admin
  const totalDocs =
    stats?.indexes.reduce((sum, idx) => sum + idx.document_count, 0) ?? 0;
  const totalChunks =
    stats?.indexes.reduce((sum, idx) => sum + idx.chunk_count, 0) ?? 0;

  // Check if offline
  const isOffline =
    statsError &&
    (statsError.message?.includes("Network Error") ||
      (statsError as { code?: string })?.code === "ERR_NETWORK");

  if (isOffline) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">
              Knowledge Base
            </h1>
            <p className="text-muted-foreground">
              Search and query indexed knowledge
            </p>
          </div>
        </div>
        <OfflineState
          title="Cannot Connect to Knowledge Base"
          description="Start the RoboCo orchestrator to access the knowledge base."
          onRetry={() => void refresh()}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Knowledge Base</h1>
          <p className="text-muted-foreground">
            Search and query indexed knowledge
          </p>
        </div>
      </div>

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onValueChange={(v) => handleTabChange(v as TabValue)}
      >
        <TabsList>
          {(
            [
              {
                value: "search",
                icon: Search,
                label: "Search",
                hint: "Semantic search across indexed content, filterable by category",
              },
              {
                value: "ask",
                icon: Sparkles,
                label: "Ask AI",
                hint: "One-shot Q&A: retrieves matching chunks (RAG) and generates a cited answer",
              },
              {
                value: "mentor",
                icon: Brain,
                label: "Mentor",
                hint: "Multi-turn chat that also factors in your role and your own journal entries",
              },
              {
                value: "browse",
                icon: FolderTree,
                label: "Browse",
                hint: "Page through every indexed document by category, no query needed",
              },
              {
                value: "admin",
                icon: Settings,
                label: "Admin",
                hint: "Index health, storage stats, and manual reindex/refresh/delete controls",
              },
            ] as const
          ).map((tab) => (
            <Tooltip key={tab.value}>
              <TooltipTrigger asChild>
                {/* TooltipTrigger's asChild Slot merge injects its own
                    data-state onto this trigger, and Radix Tabs' internal
                    render spreads incoming props after its own literal
                    data-state — so the tooltip's value silently wins and the
                    data-[state=active] highlight never fires. Re-assert the
                    real selection state explicitly so it survives the merge. */}
                <TabsTrigger
                  value={tab.value}
                  data-state={tab.value === activeTab ? "active" : "inactive"}
                  className="gap-2"
                >
                  <tab.icon className="h-4 w-4" />
                  {tab.label}
                </TabsTrigger>
              </TooltipTrigger>
              <TooltipContent>{tab.hint}</TooltipContent>
            </Tooltip>
          ))}
        </TabsList>

        {/* Search Tab */}
        <TabsContent value="search" className="mt-6">
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            {/* Sidebar */}
            <div className="lg:col-span-1 space-y-4">
              <Card>
                <CardContent className="pt-4">
                  <KBFilters
                    selectedTypes={searchFilters}
                    onTypesChange={handleFiltersChange}
                  />
                </CardContent>
              </Card>
              <KBStatsCard stats={stats} isLoading={statsLoading} />
            </div>

            {/* Main content */}
            <div className="lg:col-span-3 space-y-4">
              <KBSearchBar
                value={searchQuery}
                onChange={handleSearchChange}
                isLoading={searchLoading}
              />
              <ScrollArea className="h-[calc(100dvh-380px)]">
                <KBResultList
                  response={searchResults}
                  isLoading={searchLoading}
                  query={searchQuery}
                />
              </ScrollArea>
            </div>
          </div>
        </TabsContent>

        {/* Ask AI Tab */}
        <TabsContent value="ask" className="mt-6">
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            {/* Sidebar */}
            <div className="lg:col-span-1">
              <KBStatsCard stats={stats} isLoading={statsLoading} />
            </div>

            {/* Main content */}
            <div className="lg:col-span-3 space-y-6">
              <RAGQueryInput
                onSubmit={handleRAGQuery}
                isLoading={ragMutation.isPending}
              />
              <ScrollArea className="h-[calc(100dvh-450px)]">
                <RAGAnswerDisplay
                  response={ragResponse}
                  isLoading={ragMutation.isPending}
                  question={ragQuestion}
                  error={ragError}
                />
              </ScrollArea>
            </div>
          </div>
        </TabsContent>

        {/* Mentor Tab - Chat Interface */}
        <TabsContent value="mentor" className="mt-6">
          <MentorChat onAsk={handleMentorAsk} isLoading={askMentor.isPending} />
        </TabsContent>

        {/* Browse Tab */}
        <TabsContent value="browse" className="mt-6">
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            {/* Sidebar */}
            <div className="lg:col-span-1">
              <KBCategoryNav
                stats={stats}
                isLoading={statsLoading}
                selectedCategory={selectedCategory}
                onSelectCategory={handleCategoryChange}
              />
            </div>

            {/* Main content */}
            <div className="lg:col-span-3">
              <ScrollArea className="h-[calc(100dvh-320px)]">
                <KBCategoryView category={selectedCategory} />
              </ScrollArea>
            </div>
          </div>
        </TabsContent>

        {/* Admin Tab */}
        <TabsContent value="admin" className="mt-6">
          <div className="grid grid-cols-12 gap-6">
            {/* Left Column - Health & Mentor */}
            <div className="col-span-12 lg:col-span-4 space-y-4">
              {/* Health Card */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <Activity className="h-4 w-4" />
                    System Health
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {loadingHealth ? (
                    <Skeleton className="h-20 w-full" />
                  ) : health ? (
                    <div className="space-y-3">
                      <div className="flex items-center gap-2">
                        {health.healthy ? (
                          <CheckCircle className="h-8 w-8 text-green-600" />
                        ) : (
                          <XCircle className="h-8 w-8 text-red-600" />
                        )}
                        <div>
                          <HelpTip label="Healthy requires all three subsystems below — embedding, LLM, and vector store — to each respond OK">
                            <p className="font-medium w-fit">
                              {health.healthy ? "Healthy" : "Unhealthy"}
                            </p>
                          </HelpTip>
                          <HelpTip label="Ollama-served embedding model (qwen3-embedding:0.6b) that turns text into vectors for search">
                            <p className="text-sm text-muted-foreground w-fit">
                              Embedding: {health.embedding_status}
                            </p>
                          </HelpTip>
                        </div>
                      </div>
                      <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                        <HelpTip label="Local model that generates RAG and Mentor answers from retrieved context">
                          <div className="w-fit">LLM: {health.llm_status}</div>
                        </HelpTip>
                        <HelpTip label="PostgreSQL + pgvector store holding every embedded chunk for similarity search">
                          <div className="w-fit">
                            Vector: {health.vector_store_status}
                          </div>
                        </HelpTip>
                      </div>
                      {(
                        [
                          ["llm_error", "LLM"],
                          ["embedding_error", "Embedding"],
                          ["vector_store_error", "Vector store"],
                        ] as const
                      )
                        .filter(
                          ([k]) => typeof health.details?.[k] === "string",
                        )
                        .map(([k, label]) => (
                          <p
                            key={k}
                            className="text-xs text-red-600 dark:text-red-400 break-words"
                          >
                            <span className="font-medium">{label}:</span>{" "}
                            {health.details[k] as string}
                          </p>
                        ))}
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-sm">
                      Health data unavailable
                    </p>
                  )}
                </CardContent>
              </Card>

              {/* Summary Stats */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm flex items-center justify-between">
                    <span>Summary</span>
                    <AlertDialog>
                      <HelpTip label="Rebuilds the Documentation index from the repo's docs/ tree — the other categories (journals, conversations, etc.) are populated live by agent activity, not by this button">
                        <AlertDialogTrigger asChild>
                          <Button size="sm" variant="destructive">
                            <RefreshCw className="h-3 w-3 mr-1" />
                            Reindex All
                          </Button>
                        </AlertDialogTrigger>
                      </HelpTip>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Reindex All Data?</AlertDialogTitle>
                          <AlertDialogDescription>
                            This will rebuild all indexes from scratch. This may
                            take several minutes.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction
                            onClick={handleReindexAll}
                            disabled={reindexAll.isPending}
                          >
                            {reindexAll.isPending && (
                              <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                            )}
                            Reindex
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 gap-4">
                    <HelpTip label="Sum of documents indexed across every category below">
                      <div className="w-fit">
                        <p className="text-2xl font-bold">{totalDocs}</p>
                        <p className="text-xs text-muted-foreground">
                          Documents
                        </p>
                      </div>
                    </HelpTip>
                    <HelpTip label="A chunk is a segment of a document split for embedding — one document can produce many chunks">
                      <div className="w-fit">
                        <p className="text-2xl font-bold">{totalChunks}</p>
                        <p className="text-xs text-muted-foreground">
                          Chunks
                        </p>
                      </div>
                    </HelpTip>
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Right Column - Index Management */}
            <div className="col-span-12 lg:col-span-8">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <HardDrive className="h-4 w-4" />
                    Index Management
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {statsLoading ? (
                    <div className="space-y-3">
                      {Array.from({ length: 5 }).map((_, i) => (
                        <Skeleton key={i} className="h-16 w-full" />
                      ))}
                    </div>
                  ) : (
                    <ScrollArea className="h-[450px]">
                      <div className="space-y-3 pr-4">
                        {stats?.indexes.map((index) => {
                          const indexType = index.index_type;
                          const percentage =
                            totalChunks > 0
                              ? Math.round(
                                  (index.chunk_count / totalChunks) * 100,
                                )
                              : 0;

                          return (
                            <div
                              key={indexType}
                              className="border rounded-lg p-4 hover:bg-muted/50 transition-colors"
                            >
                              <div className="flex items-center justify-between mb-2">
                                <div className="flex items-center gap-2">
                                  <FileText className="h-4 w-4 text-muted-foreground" />
                                  <span className="font-medium">
                                    {INDEX_LABELS[indexType]}
                                  </span>
                                  <HelpTip
                                    label={getIndexTypeDescription(indexType)}
                                  >
                                    <Badge
                                      variant="outline"
                                      className="text-xs w-fit"
                                    >
                                      {indexType}
                                    </Badge>
                                  </HelpTip>
                                </div>
                                <div className="flex items-center gap-2">
                                  <HelpTip label="Refresh this index">
                                    <Button
                                      size="sm"
                                      variant="outline"
                                      onClick={() =>
                                        handleRefreshIndex(indexType)
                                      }
                                      disabled={refreshIndex.isPending}
                                      aria-label={`Refresh ${INDEX_LABELS[indexType]} index`}
                                    >
                                      {refreshIndex.isPending ? (
                                        <RefreshCw className="h-3 w-3 animate-spin" />
                                      ) : (
                                        <RefreshCw className="h-3 w-3" />
                                      )}
                                    </Button>
                                  </HelpTip>
                                  <AlertDialog>
                                    <HelpTip label="Delete this index and all its documents">
                                      <AlertDialogTrigger asChild>
                                        <Button
                                          size="sm"
                                          variant="outline"
                                          className="text-red-600"
                                          aria-label={`Delete ${INDEX_LABELS[indexType]} index`}
                                        >
                                          <Trash2 className="h-3 w-3" />
                                        </Button>
                                      </AlertDialogTrigger>
                                    </HelpTip>
                                    <AlertDialogContent>
                                      <AlertDialogHeader>
                                        <AlertDialogTitle>
                                          Delete {INDEX_LABELS[indexType]}?
                                        </AlertDialogTitle>
                                        <AlertDialogDescription>
                                          This will permanently delete all{" "}
                                          {index.document_count} documents and{" "}
                                          {index.chunk_count} chunks from this
                                          index.
                                        </AlertDialogDescription>
                                      </AlertDialogHeader>
                                      <AlertDialogFooter>
                                        <AlertDialogCancel>
                                          Cancel
                                        </AlertDialogCancel>
                                        <AlertDialogAction
                                          onClick={() =>
                                            handleDeleteIndex(indexType)
                                          }
                                          className="bg-red-600 hover:bg-red-700"
                                        >
                                          Delete
                                        </AlertDialogAction>
                                      </AlertDialogFooter>
                                    </AlertDialogContent>
                                  </AlertDialog>
                                </div>
                              </div>

                              <div className="grid grid-cols-3 gap-4 text-sm mb-2">
                                <HelpTip label="Document count for this category — see Chunks for the post-embedding split count">
                                  <div className="w-fit">
                                    <span className="text-muted-foreground">
                                      Documents:
                                    </span>{" "}
                                    <span className="font-medium">
                                      {index.document_count}
                                    </span>
                                  </div>
                                </HelpTip>
                                <HelpTip label="A chunk is a segment of a document split for embedding — one document can produce many chunks">
                                  <div className="w-fit">
                                    <span className="text-muted-foreground">
                                      Chunks:
                                    </span>{" "}
                                    <span className="font-medium">
                                      {index.chunk_count}
                                    </span>
                                  </div>
                                </HelpTip>
                                <HelpTip
                                  label={
                                    index.last_updated
                                      ? new Date(
                                          index.last_updated,
                                        ).toLocaleString()
                                      : "This index has never been populated"
                                  }
                                >
                                  <div className="w-fit">
                                    <span className="text-muted-foreground">
                                      Updated:
                                    </span>{" "}
                                    <span className="font-medium">
                                      {index.last_updated
                                        ? formatDistanceToNow(
                                            new Date(index.last_updated),
                                          ) + " ago"
                                        : "Never"}
                                    </span>
                                  </div>
                                </HelpTip>
                              </div>

                              <div className="space-y-1">
                                <HelpTip label="Share of the fleet's total chunk count this category holds — not literal disk bytes">
                                  <div className="flex justify-between text-xs text-muted-foreground">
                                    <span>Storage usage</span>
                                    <span>{percentage}%</span>
                                  </div>
                                </HelpTip>
                                <Progress value={percentage} className="h-1" />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </ScrollArea>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// Loading skeleton for Suspense fallback
function KnowledgeBaseBrowserSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Skeleton className="h-9 w-48 mb-2" />
          <Skeleton className="h-5 w-64" />
        </div>
        <Skeleton className="h-10 w-24" />
      </div>
      <Skeleton className="h-10 w-64" />
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-1 space-y-4">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
        <div className="lg:col-span-3 space-y-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export function KnowledgeBaseBrowser() {
  return (
    <Suspense fallback={<KnowledgeBaseBrowserSkeleton />}>
      <KnowledgeBaseBrowserContent />
    </Suspense>
  );
}
