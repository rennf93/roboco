"use client";

import { useState } from "react";
import { useChannels } from "@/hooks/use-channels";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ChannelSidebar } from "./channel-sidebar";
import { RefreshCw, Hash, Users, ExternalLink } from "lucide-react";
import Link from "next/link";

export function CommunicationsView() {
  const [selectedChannelId, setSelectedChannelId] = useState<string | null>(null);
  const { data: channels, isLoading: loadingChannels, refetch } = useChannels();

  // Get selected channel
  const selectedChannel = channels?.find((c) => c.id === selectedChannelId);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Communications</h1>
          <p className="text-muted-foreground">
            Browse channels and view messages
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/communications">
            <Button variant="outline">
              <ExternalLink className="h-4 w-4 mr-2" />
              Full View
            </Button>
          </Link>
          <Button variant="outline" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-12 gap-6 h-[calc(100vh-220px)]">
        {/* Channel Sidebar */}
        <div className="col-span-12 lg:col-span-3">
          <Card className="h-full">
            <CardHeader className="py-3">
              <CardTitle className="text-sm font-medium">Channels</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <ChannelSidebar
                channels={channels}
                isLoading={loadingChannels}
                selectedChannelId={selectedChannelId}
                onSelectChannel={setSelectedChannelId}
              />
            </CardContent>
          </Card>
        </div>

        {/* Channel Info Area */}
        <div className="col-span-12 lg:col-span-9">
          <Card className="h-full flex flex-col">
            {selectedChannel ? (
              <>
                {/* Channel Header */}
                <CardHeader className="py-3 border-b shrink-0">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Hash className="h-5 w-5 text-muted-foreground" />
                      <CardTitle className="text-lg">{selectedChannel.name}</CardTitle>
                      <Badge variant="outline" className="text-xs">
                        <Users className="h-3 w-3 mr-1" />
                        {selectedChannel.member_count}
                      </Badge>
                    </div>
                    <Link href={`/communications?channel=${selectedChannel.id}`}>
                      <Button variant="outline" size="sm">
                        <ExternalLink className="h-4 w-4 mr-2" />
                        Open Channel
                      </Button>
                    </Link>
                  </div>
                  {selectedChannel.description && (
                    <p className="text-sm text-muted-foreground">
                      {selectedChannel.description}
                    </p>
                  )}
                </CardHeader>

                {/* Channel Stats */}
                <CardContent className="flex-1 flex items-center justify-center">
                  <div className="text-center space-y-4">
                    <div className="grid grid-cols-2 gap-8">
                      <div>
                        <p className="text-3xl font-bold">{selectedChannel.message_count}</p>
                        <p className="text-sm text-muted-foreground">Messages</p>
                      </div>
                      <div>
                        <p className="text-3xl font-bold">{selectedChannel.group_count}</p>
                        <p className="text-sm text-muted-foreground">Groups</p>
                      </div>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      Open the channel to view sessions and send messages
                    </p>
                    <Link href={`/communications?channel=${selectedChannel.id}`}>
                      <Button>
                        View Sessions
                      </Button>
                    </Link>
                  </div>
                </CardContent>
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center text-muted-foreground">
                <div className="text-center">
                  <Hash className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p className="text-lg font-medium">Select a Channel</p>
                  <p className="text-sm">Choose a channel from the sidebar to view details</p>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
