"use client";

import { Suspense, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  useChannels,
  useChannelGroups,
  useGroupSessions,
} from "@/hooks/use-channels";
import type { Channel } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { OfflineState } from "@/components/ui/offline-state";
import {
  Hash,
  Lock,
  Users,
  MessageSquare,
  RefreshCw,
  Folder,
  MessageCircle,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import Link from "next/link";

// =============================================================================
// Channel List Panel
// =============================================================================

interface ChannelListProps {
  channels: Channel[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  isLoading: boolean;
}

function ChannelList({ channels, selectedId, onSelect, isLoading }: ChannelListProps) {
  const cellChannels = channels.filter((c) => c.type === "cell");
  const crossCellChannels = channels.filter((c) => c.type === "cross_cell");
  const managementChannels = channels.filter((c) => c.type === "management");
  const otherChannels = channels.filter(
    (c) => !["cell", "cross_cell", "management"].includes(c.type)
  );

  const renderGroup = (title: string, items: Channel[]) => {
    if (items.length === 0) return null;
    return (
      <div className="mb-4">
        <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 px-2">
          {title}
        </h4>
        {items.map((channel) => (
          <button
            key={channel.id}
            onClick={() => onSelect(channel.id)}
            className={
              "w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left transition-colors " +
              (selectedId === channel.id
                ? "bg-primary text-primary-foreground"
                : "hover:bg-muted")
            }
          >
            {channel.is_private ? (
              <Lock className="h-4 w-4 shrink-0" />
            ) : (
              <Hash className="h-4 w-4 shrink-0" />
            )}
            <span className="truncate">{channel.name}</span>
          </button>
        ))}
      </div>
    );
  };

  if (isLoading) {
    return (
      <div className="p-2 space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-2">
        {renderGroup("Cell Channels", cellChannels)}
        {renderGroup("Cross-Cell", crossCellChannels)}
        {renderGroup("Management", managementChannels)}
        {renderGroup("Other", otherChannels)}
      </div>
    </ScrollArea>
  );
}

// =============================================================================
// Group List Panel
// =============================================================================

interface GroupListProps {
  channelId: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

function GroupList({ channelId, selectedId, onSelect }: GroupListProps) {
  const { data: groups, isLoading } = useChannelGroups(channelId);

  if (isLoading) {
    return (
      <div className="p-2 space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (!groups || groups.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center p-4">
          <Folder className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No groups in this channel</p>
        </div>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-2 space-y-1">
        {groups.map((group) => (
          <button
            key={group.id}
            onClick={() => onSelect(group.id)}
            className={
              "w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm text-left transition-colors " +
              (selectedId === group.id
                ? "bg-primary text-primary-foreground"
                : "hover:bg-muted")
            }
          >
            <div className="flex items-center gap-2 min-w-0">
              <Users className="h-4 w-4 shrink-0" />
              <span className="truncate">{group.name}</span>
            </div>
            <Badge variant="secondary" className="text-xs shrink-0 ml-2">
              {group.total_messages}
            </Badge>
          </button>
        ))}
      </div>
    </ScrollArea>
  );
}

// =============================================================================
// Session List Panel
// =============================================================================

interface SessionListProps {
  channelId: string;
  groupId: string;
}

function SessionList({ channelId, groupId }: SessionListProps) {
  const { data: sessions, isLoading } = useGroupSessions(groupId);

  if (isLoading) {
    return (
      <div className="p-2 space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (!sessions || sessions.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center p-4">
          <MessageCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No sessions in this group</p>
        </div>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-2 space-y-2">
        {sessions.map((session) => (
          <Link
            key={session.id}
            href={`/communications/${session.id}?channel=${channelId}&group=${groupId}`}
            className="block p-3 rounded-lg border bg-card hover:bg-muted/50 hover:border-primary/50 transition-all"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="font-medium text-sm truncate">
                  {session.task_links?.length > 0 ? (
                    <>
                      {session.task_links.find(l => l.is_primary)?.task_title ||
                       session.task_links[0]?.task_title ||
                       `Task ${session.task_links[0]?.task_id.slice(0, 8)}`}
                    </>
                  ) : (
                    `Session ${session.id.slice(0, 8)}`
                  )}
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {formatDistanceToNow(new Date(session.started_at))} ago
                </div>
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0">
                <Badge
                  variant={session.status === "active" ? "default" : "secondary"}
                  className="text-xs"
                >
                  {session.status}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {session.message_count} msgs
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </ScrollArea>
  );
}

// =============================================================================
// Empty State Components
// =============================================================================

function EmptyPanel({ icon: Icon, message }: { icon: typeof MessageSquare; message: string }) {
  return (
    <div className="h-full flex items-center justify-center text-muted-foreground">
      <div className="text-center p-4">
        <Icon className="h-8 w-8 mx-auto mb-2 opacity-50" />
        <p className="text-sm">{message}</p>
      </div>
    </div>
  );
}

// =============================================================================
// Main Page
// =============================================================================

function CommunicationsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const channelId = searchParams.get("channel");
  const groupId = searchParams.get("group");

  const { data: channels, isLoading, error, refetch } = useChannels();

  const isOffline = error && (
    error.message?.includes("Network Error") ||
    (error as { code?: string })?.code === "ERR_NETWORK"
  );

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
      router.push(query ? `/communications?${query}` : "/communications");
    },
    [router, searchParams]
  );

  const handleSelectChannel = useCallback((id: string) => {
    updateParams({ channel: id, group: null });
  }, [updateParams]);

  const handleSelectGroup = useCallback((id: string) => {
    updateParams({ group: id });
  }, [updateParams]);

  const selectedChannel = channels?.find(c => c.id === channelId);

  return (
    <div className="flex flex-col h-[calc(100vh-7rem)]">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Communications</h1>
          <p className="text-muted-foreground">
            Browse channels, groups, and sessions
          </p>
        </div>
        <Button variant="outline" onClick={() => refetch()}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      {isOffline ? (
        <OfflineState
          title="Cannot Load Channels"
          description="Start the RoboCo orchestrator to view communications."
          onRetry={() => refetch()}
        />
      ) : (
        <div className="grid grid-cols-12 gap-6 flex-1 min-h-0">
          {/* Panel 1: Channels */}
          <Card className="col-span-3 flex flex-col overflow-hidden">
            <CardContent className="p-3 flex flex-col h-full">
              <div className="flex items-center gap-2 mb-3 pb-2 border-b">
                <Hash className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">Channels</span>
              </div>
              <div className="flex-1 overflow-hidden -mx-3">
                <ChannelList
                  channels={channels || []}
                  selectedId={channelId}
                  onSelect={handleSelectChannel}
                  isLoading={isLoading}
                />
              </div>
            </CardContent>
          </Card>

          {/* Panel 2: Groups */}
          <Card className="col-span-3 flex flex-col overflow-hidden">
            <CardContent className="p-3 flex flex-col h-full">
              <div className="flex items-center gap-2 mb-3 pb-2 border-b">
                <Users className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">Groups</span>
                {selectedChannel && (
                  <Badge variant="outline" className="ml-auto text-xs font-normal">
                    {selectedChannel.name}
                  </Badge>
                )}
              </div>
              <div className="flex-1 overflow-hidden -mx-3">
                {channelId ? (
                  <GroupList
                    channelId={channelId}
                    selectedId={groupId}
                    onSelect={handleSelectGroup}
                  />
                ) : (
                  <EmptyPanel icon={Folder} message="Select a channel" />
                )}
              </div>
            </CardContent>
          </Card>

          {/* Panel 3: Sessions */}
          <Card className="col-span-6 flex flex-col overflow-hidden">
            <CardContent className="p-3 flex flex-col h-full">
              <div className="flex items-center gap-2 mb-3 pb-2 border-b">
                <MessageCircle className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">Sessions</span>
              </div>
              <div className="flex-1 overflow-hidden -mx-3">
                {channelId && groupId ? (
                  <SessionList channelId={channelId} groupId={groupId} />
                ) : (
                  <EmptyPanel
                    icon={MessageSquare}
                    message={channelId ? "Select a group" : "Select a channel and group"}
                  />
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function CommunicationsPage() {
  return (
    <Suspense fallback={
      <div className="flex flex-col h-[calc(100vh-7rem)]">
        <div className="flex items-center justify-between mb-4">
          <div>
            <Skeleton className="h-9 w-48 mb-2" />
            <Skeleton className="h-5 w-64" />
          </div>
        </div>
        <div className="grid grid-cols-12 gap-6 flex-1">
          <Card className="col-span-3">
            <CardContent className="p-3 space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </CardContent>
          </Card>
          <Card className="col-span-3">
            <CardContent className="p-3 space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </CardContent>
          </Card>
          <Card className="col-span-6" />
        </div>
      </div>
    }>
      <CommunicationsPageContent />
    </Suspense>
  );
}
