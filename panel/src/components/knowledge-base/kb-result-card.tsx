"use client";

import { Card, CardContent } from "@/components/ui/card";
import { KBSearchResult } from "@/types";
import { KBIndexTypeBadge } from "./kb-index-type-badge";
import { ExternalLink, FileCode, Hash } from "lucide-react";

interface KBResultCardProps {
  result: KBSearchResult;
  onClick?: () => void;
}

export function KBResultCard({ result, onClick }: KBResultCardProps) {
  // Truncate content for display (snippet)
  const snippet = result.content.length > 300
    ? result.content.substring(0, 300) + "..."
    : result.content;

  // Format source for display
  const formatSource = (source: string) => {
    // Remove common prefixes
    if (source.startsWith("channel:")) {
      return source.replace("channel:", "# ");
    }
    if (source.startsWith("journal:")) {
      return source.replace("journal:", "Journal: ");
    }
    // Shorten file paths
    if (source.includes("/")) {
      const parts = source.split("/");
      if (parts.length > 3) {
        return ".../" + parts.slice(-2).join("/");
      }
    }
    return source;
  };

  // Score as percentage
  const scorePercent = Math.round(result.score * 100);

  return (
    <Card
      className={`hover:bg-muted/50 transition-colors ${onClick ? "cursor-pointer" : ""}`}
      onClick={onClick}
    >
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            {/* Header */}
            <div className="flex items-center gap-2 flex-wrap mb-2">
              <KBIndexTypeBadge indexType={result.index_type} />
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Hash className="h-3 w-3" />
                {scorePercent}% match
              </span>
            </div>

            {/* Source */}
            <div className="flex items-center gap-1 text-sm text-muted-foreground mb-2">
              <FileCode className="h-3 w-3 shrink-0" />
              <span className="truncate font-mono text-xs">{formatSource(result.source)}</span>
            </div>

            {/* Content snippet */}
            <p className="text-sm text-foreground/90 whitespace-pre-wrap line-clamp-4">
              {snippet}
            </p>

            {/* Metadata */}
            {result.metadata && Object.keys(result.metadata).length > 0 && (
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                {typeof result.metadata.language === "string" && (
                  <span className="text-xs bg-muted px-1.5 py-0.5 rounded">
                    {result.metadata.language}
                  </span>
                )}
                {typeof result.metadata.agent === "string" && (
                  <span className="text-xs bg-muted px-1.5 py-0.5 rounded">
                    @{result.metadata.agent}
                  </span>
                )}
                {typeof result.metadata.section === "string" && (
                  <span className="text-xs bg-muted px-1.5 py-0.5 rounded">
                    {result.metadata.section}
                  </span>
                )}
              </div>
            )}
          </div>

          {onClick && (
            <ExternalLink className="h-4 w-4 text-muted-foreground shrink-0" />
          )}
        </div>
      </CardContent>
    </Card>
  );
}
