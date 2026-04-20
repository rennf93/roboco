"use client";

import { ChannelFeed } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Radio } from "lucide-react";
import { LiveFeedItem } from "./live-feed-item";

interface LiveFeedsPanelProps {
  feeds: ChannelFeed[] | undefined;
  isLoading: boolean;
}

export function LiveFeedsPanel({ feeds, isLoading }: LiveFeedsPanelProps) {
  const activeCount = (feeds ?? []).filter(
    (f) => f.status === "active" || f.message_count_24h > 0
  ).length;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <Radio className="h-5 w-5" />
            Live Feeds
          </CardTitle>
          <span className="text-sm text-muted-foreground">
            {activeCount} active
          </span>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <Skeleton key={i} className="h-14" />
            ))}
          </div>
        ) : !feeds || feeds.length === 0 ? (
          <div className="text-center py-4 text-muted-foreground text-sm">
            <Radio className="h-8 w-8 mx-auto mb-2 opacity-50" />
            No channel feeds available
          </div>
        ) : (
          <div className="space-y-2">
            {feeds.map((feed) => (
              <LiveFeedItem key={feed.id} feed={feed} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
