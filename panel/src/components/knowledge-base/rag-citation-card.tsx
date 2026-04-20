"use client";

import { Card, CardContent } from "@/components/ui/card";
import { RAGCitation } from "@/types";
import { KBIndexTypeBadge } from "./kb-index-type-badge";
import { Quote, Hash } from "lucide-react";

interface RAGCitationCardProps {
  citation: RAGCitation;
  index: number;
}

export function RAGCitationCard({ citation, index }: RAGCitationCardProps) {
  // Truncate content
  const snippet = citation.content.length > 200
    ? citation.content.substring(0, 200) + "..."
    : citation.content;

  // Format source
  const formatSource = (source: string) => {
    if (source.includes("/")) {
      const parts = source.split("/");
      if (parts.length > 3) {
        return ".../" + parts.slice(-2).join("/");
      }
    }
    return source;
  };

  const scorePercent = Math.round(citation.score * 100);

  return (
    <Card className="bg-muted/30">
      <CardContent className="p-3">
        <div className="flex items-start gap-2">
          <div className="flex items-center justify-center w-5 h-5 rounded-full bg-primary/10 text-primary text-xs font-medium shrink-0">
            {index + 1}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <KBIndexTypeBadge indexType={citation.index_type} className="text-xs" />
              <span className="text-xs text-muted-foreground flex items-center gap-0.5">
                <Hash className="h-3 w-3" />
                {scorePercent}%
              </span>
            </div>
            <p className="text-xs text-muted-foreground font-mono truncate mb-1">
              {formatSource(citation.source)}
            </p>
            <div className="flex items-start gap-1">
              <Quote className="h-3 w-3 text-muted-foreground shrink-0 mt-0.5" />
              <p className="text-sm text-foreground/80 line-clamp-3">{snippet}</p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
