"use client";

import { Channel } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Hash, Lock } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChannelItemProps {
  channel: Channel;
  isSelected: boolean;
  onClick: () => void;
  unreadCount?: number;
}

export function ChannelItem({
  channel,
  isSelected,
  onClick,
  unreadCount = 0,
}: ChannelItemProps) {
  return (
    <Button
      onClick={onClick}
      variant="ghost"
      className={cn(
        "w-full h-auto justify-start gap-2 px-2 py-1.5 font-normal whitespace-normal",
        isSelected
          ? "bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {channel.is_private ? (
        <Lock className="h-4 w-4 shrink-0" />
      ) : (
        <Hash className="h-4 w-4 shrink-0" />
      )}
      <span className="flex-1 truncate text-sm">{channel.name}</span>
      {unreadCount > 0 && (
        <Badge variant="destructive" className="h-5 px-1.5 text-xs">
          {unreadCount}
        </Badge>
      )}
    </Button>
  );
}
