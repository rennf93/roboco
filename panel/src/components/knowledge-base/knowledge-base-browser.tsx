"use client";

import { useState, useCallback, Suspense } from "react";
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
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";
import { getErrorMessage } from "@/lib/api/client";

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

type TabValue = "search" | "ask" | "mentor" | "browse" | "admin";

const INDEX_LABELS: Record<KBIndexType, string> = {
  [KBIndexType.CODE]: "Codebase",
  [KBIndexType.DOCUMENTATION]: "Documentation",
  [KBIndexType.CONVERSATIONS]: "Conversations",
  [KBIndexType.JOURNALS]: "Agent Journals",
  [KBIndexType.ERRORS]: "Error Solutions",
  [KBIndexType.STANDARDS]: "Standards",
  [KBIndexType.DECISIONS]: "Decisions",
  [KBIndexType.REVIEWS]: "Code Reviews",
  [KBIndexType.LEARNINGS]: "Learnings",
};

// Valid KB index types for URL param validation
const VALID_INDEX_TYPES: KBIndexType[] = [
  KBIndexType.CODE,
  KBIndexType.DOCUMENTATION,
  KBIndexType.CONVERSATIONS,
  KBIndexType.JOURNALS,
  KBIndexType.ERRORS,
  KBIndexType.STANDARDS,
  KBIndexType.DECISIONS,
  KBIndexType.REVIEWS,
  KBIndexType.LEARNINGS,
];

function KnowledgeBaseBrowserContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read state from URL params
  const activeTab = (searchParams.get("tab") as TabValue) || "search";
  const searchQuery = searchParams.get("q") || "";
  const filtersParam = searchParams.get("filters");
  const searchFilters: KBIndexType[] = filtersParam
    ? (filtersParam.split(",").filter((f) => VALID_INDEX_TYPES.includes(f as KBIndexType)) as KBIndexType[])
    : [];
  const selectedCategory = (searchParams.get("category") as KBIndexType) || null;

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
    [router, searchParams]
  );

  // State update handlers
  const handleTabChange = useCallback(
    (tab: TabValue) => {
      updateParams({ tab: tab === "search" ? null : tab });
    },
    [updateParams]
  );

  const handleSearchChange = useCallback(
    (query: string) => {
      updateParams({ q: query || null });
    },
    [updateParams]
  );

  const handleFiltersChange = useCallback(
    (filters: KBIndexType[]) => {
      updateParams({ filters: filters.length > 0 ? filters.join(",") : null });
    },
    [updateParams]
  );

  const handleCategoryChange = useCallback(
    (category: KBIndexType | null) => {
      updateParams({ category });
    },
    [updateParams]
  );

  // Data hooks
  const { data: stats, isLoading: statsLoading, error: statsError, refetch: refetchStats } = useKBStats();
  const { data: searchResults, isLoading: searchLoading } = useKBSearch({
    query: searchQuery,
    index_types: searchFilters.length > 0 ? searchFilters : undefined,
  });
  const ragMutation = useRAGQuery();

  // Admin hooks
  const { data: health, isLoading: loadingHealth, refetch: refetchHealth } = useRAGHealth();
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
        const codeCount = result.code?.successful ?? 0;
        const docsCount = result.documentation?.successful ?? 0;
        toast.success(
          `Reindexed ${codeCount} code files, ${docsCount} docs. ` +
            `${result.warnings?.length ? `Warnings: ${result.warnings.length}` : ""}`
        );
      } else {
        toast.warning(
          `Reindex completed with issues: ${result.warnings?.join(", ") ?? "Unknown errors"}`
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

  const handleRefresh = () => {
    refetchStats();
    refetchHealth();
  };

  // Calculate totals for admin
  const totalDocs = stats?.indexes.reduce((sum, idx) => sum + idx.document_count, 0) ?? 0;
  const totalChunks = stats?.indexes.reduce((sum, idx) => sum + idx.chunk_count, 0) ?? 0;

  // Check if offline
  const isOffline = statsError && (
    statsError.message?.includes("Network Error") ||
    (statsError as { code?: string })?.code === "ERR_NETWORK"
  );

  if (isOffline) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Knowledge Base</h1>
            <p className="text-muted-foreground">
              Search and query indexed knowledge
            </p>
          </div>
        </div>
        <OfflineState
          title="Cannot Connect to Knowledge Base"
          description="Start the RoboCo orchestrator to access the knowledge base."
          onRetry={() => refetchStats()}
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
        <Button variant="outline" onClick={handleRefresh}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={(v) => handleTabChange(v as TabValue)}>
        <TabsList>
          <TabsTrigger value="search" className="gap-2">
            <Search className="h-4 w-4" />
            Search
          </TabsTrigger>
          <TabsTrigger value="ask" className="gap-2">
            <Sparkles className="h-4 w-4" />
            Ask AI
          </TabsTrigger>
          <TabsTrigger value="mentor" className="gap-2">
            <Brain className="h-4 w-4" />
            Mentor
          </TabsTrigger>
          <TabsTrigger value="browse" className="gap-2">
            <FolderTree className="h-4 w-4" />
            Browse
          </TabsTrigger>
          <TabsTrigger value="admin" className="gap-2">
            <Settings className="h-4 w-4" />
            Admin
          </TabsTrigger>
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
              <ScrollArea className="h-[calc(100vh-380px)]">
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
              <ScrollArea className="h-[calc(100vh-450px)]">
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
          <MentorChat
            onAsk={handleMentorAsk}
            isLoading={askMentor.isPending}
          />
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
              <ScrollArea className="h-[calc(100vh-320px)]">
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
                          <p className="font-medium">{health.healthy ? "Healthy" : "Unhealthy"}</p>
                          <p className="text-sm text-muted-foreground">
                            Embedding: {health.embedding_status}
                          </p>
                        </div>
                      </div>
                      <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                        <div>LLM: {health.llm_status}</div>
                        <div>Vector: {health.vector_store_status}</div>
                      </div>
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-sm">Health data unavailable</p>
                  )}
                </CardContent>
              </Card>

              {/* Summary Stats */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm flex items-center justify-between">
                    <span>Summary</span>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button size="sm" variant="destructive">
                          <RefreshCw className="h-3 w-3 mr-1" />
                          Reindex All
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Reindex All Data?</AlertDialogTitle>
                          <AlertDialogDescription>
                            This will rebuild all indexes from scratch. This may take several minutes.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction onClick={handleReindexAll} disabled={reindexAll.isPending}>
                            {reindexAll.isPending && <RefreshCw className="h-4 w-4 mr-2 animate-spin" />}
                            Reindex
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-2xl font-bold">{totalDocs}</p>
                      <p className="text-xs text-muted-foreground">Documents</p>
                    </div>
                    <div>
                      <p className="text-2xl font-bold">{totalChunks}</p>
                      <p className="text-xs text-muted-foreground">Chunks</p>
                    </div>
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
                          const percentage = totalChunks > 0
                            ? Math.round((index.chunk_count / totalChunks) * 100)
                            : 0;

                          return (
                            <div
                              key={indexType}
                              className="border rounded-lg p-4 hover:bg-muted/50 transition-colors"
                            >
                              <div className="flex items-center justify-between mb-2">
                                <div className="flex items-center gap-2">
                                  <FileText className="h-4 w-4 text-muted-foreground" />
                                  <span className="font-medium">{INDEX_LABELS[indexType]}</span>
                                  <Badge variant="outline" className="text-xs">
                                    {indexType}
                                  </Badge>
                                </div>
                                <div className="flex items-center gap-2">
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() => handleRefreshIndex(indexType)}
                                    disabled={refreshIndex.isPending}
                                  >
                                    {refreshIndex.isPending ? (
                                      <RefreshCw className="h-3 w-3 animate-spin" />
                                    ) : (
                                      <RefreshCw className="h-3 w-3" />
                                    )}
                                  </Button>
                                  <AlertDialog>
                                    <AlertDialogTrigger asChild>
                                      <Button size="sm" variant="outline" className="text-red-600">
                                        <Trash2 className="h-3 w-3" />
                                      </Button>
                                    </AlertDialogTrigger>
                                    <AlertDialogContent>
                                      <AlertDialogHeader>
                                        <AlertDialogTitle>Delete {INDEX_LABELS[indexType]}?</AlertDialogTitle>
                                        <AlertDialogDescription>
                                          This will permanently delete all {index.document_count} documents
                                          and {index.chunk_count} chunks from this index.
                                        </AlertDialogDescription>
                                      </AlertDialogHeader>
                                      <AlertDialogFooter>
                                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                                        <AlertDialogAction
                                          onClick={() => handleDeleteIndex(indexType)}
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
                                <div>
                                  <span className="text-muted-foreground">Documents:</span>{" "}
                                  <span className="font-medium">{index.document_count}</span>
                                </div>
                                <div>
                                  <span className="text-muted-foreground">Chunks:</span>{" "}
                                  <span className="font-medium">{index.chunk_count}</span>
                                </div>
                                <div>
                                  <span className="text-muted-foreground">Updated:</span>{" "}
                                  <span className="font-medium">
                                    {index.last_updated
                                      ? formatDistanceToNow(new Date(index.last_updated)) + " ago"
                                      : "Never"}
                                  </span>
                                </div>
                              </div>

                              <div className="space-y-1">
                                <div className="flex justify-between text-xs text-muted-foreground">
                                  <span>Storage usage</span>
                                  <span>{percentage}%</span>
                                </div>
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
