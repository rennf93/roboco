"use client";

import { Channel } from "@/types";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { ChannelItem } from "./channel-item";

interface ChannelSidebarProps {
  channels: Channel[] | undefined;
  isLoading: boolean;
  selectedChannelId: string | null;
  onSelectChannel: (channelId: string) => void;
}

// Group channels by type
function groupChannels(channels: Channel[]): Record<string, Channel[]> {
  const groups: Record<string, Channel[]> = {
    "Cell Channels": [],
    "Cross-Cell": [],
    Management: [],
    Special: [],
  };

  channels.forEach((channel) => {
    if (channel.name.includes("-cell")) {
      groups["Cell Channels"].push(channel);
    } else if (channel.name.includes("-all")) {
      groups["Cross-Cell"].push(channel);
    } else if (
      channel.name.includes("pm") ||
      channel.name.includes("board")
    ) {
      groups["Management"].push(channel);
    } else {
      groups["Special"].push(channel);
    }
  });

  return groups;
}

export function ChannelSidebar({
  channels,
  isLoading,
  selectedChannelId,
  onSelectChannel,
}: ChannelSidebarProps) {
  if (isLoading) {
    return (
      <div className="space-y-2 p-2">
        {[...Array(8)].map((_, i) => (
          <Skeleton key={i} className="h-8" />
        ))}
      </div>
    );
  }

  if (!channels || channels.length === 0) {
    return (
      <div className="p-4 text-center text-muted-foreground text-sm">
        No channels available
      </div>
    );
  }

  const grouped = groupChannels(channels);

  return (
    <ScrollArea className="h-[calc(100vh-200px)]">
      <div className="p-2 space-y-4">
        {Object.entries(grouped).map(([group, groupChannels]) => {
          if (groupChannels.length === 0) return null;
          return (
            <div key={group}>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider px-2 mb-2">
                {group}
              </h3>
              <div className="space-y-0.5">
                {groupChannels.map((channel) => (
                  <ChannelItem
                    key={channel.id}
                    channel={channel}
                    isSelected={selectedChannelId === channel.id}
                    onClick={() => onSelectChannel(channel.id)}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </ScrollArea>
  );
}
