"use client";

import { KBSearchResponse } from "@/types";
import { KBResultCard } from "./kb-result-card";
import { Skeleton } from "@/components/ui/skeleton";
import { SearchX, FileSearch } from "lucide-react";

interface KBResultListProps {
  response: KBSearchResponse | undefined;
  isLoading: boolean;
  query: string;
}

export function KBResultList({ response, isLoading, query }: KBResultListProps) {
  if (isLoading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="border rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <Skeleton className="h-5 w-16" />
              <Skeleton className="h-4 w-20" />
            </div>
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-16 w-full" />
          </div>
        ))}
      </div>
    );
  }

  if (!query || query.length < 3) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <FileSearch className="h-12 w-12 text-muted-foreground/50 mb-4" />
        <h3 className="text-lg font-medium mb-1">Search the Knowledge Base</h3>
        <p className="text-sm text-muted-foreground max-w-md">
          Enter at least 3 characters to search across indexed code, documentation,
          conversations, and journals.
        </p>
      </div>
    );
  }

  if (!response || response.results.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <SearchX className="h-12 w-12 text-muted-foreground/50 mb-4" />
        <h3 className="text-lg font-medium mb-1">No results found</h3>
        <p className="text-sm text-muted-foreground max-w-md">
          No matches for &ldquo;{query}&rdquo;. Try different keywords or adjust your filters.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="text-sm text-muted-foreground mb-4">
        Found {response.total} result{response.total !== 1 ? "s" : ""} for &ldquo;{response.query}&rdquo;
      </div>
      {response.results.map((result, index) => (
        <KBResultCard key={`${result.source}-${index}`} result={result} />
      ))}
    </div>
  );
}
