"use client";

import { ChannelFeed } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Radio, Clock } from "lucide-react";

interface LiveFeedItemProps {
  feed: ChannelFeed;
}

function formatTime(timestamp: string | null): string {
  if (!timestamp) return "No activity";
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));

  if (diffMins < 1) return "Active now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function LiveFeedItem({ feed }: LiveFeedItemProps) {
  const isActive = feed.status === "active" || feed.message_count_24h > 0;

  return (
    <div className="flex items-center justify-between p-3 rounded-lg border bg-muted/30 hover:bg-muted/50 transition-colors">
      <div className="flex items-center gap-3">
        <Radio
          className={`h-4 w-4 ${isActive ? "text-green-500 animate-pulse" : "text-gray-400"}`}
        />
        <div>
          <span className="font-medium text-sm">#{feed.name}</span>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Clock className="h-3 w-3" />
            {formatTime(feed.last_activity)}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Badge variant={isActive ? "default" : "secondary"} className="text-xs">
          {feed.message_count_24h} msgs
        </Badge>
        <Badge
          variant="outline"
          className={isActive ? "text-green-600 border-green-300" : ""}
        >
          {isActive ? "Active" : "Idle"}
        </Badge>
      </div>
    </div>
  );
}
