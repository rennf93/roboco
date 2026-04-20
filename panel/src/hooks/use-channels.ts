"use client";

import { useQuery } from "@tanstack/react-query";
import { channelsApi, type ChannelFilters } from "@/lib/api/channels";
import { sessionsApi } from "@/lib/api/sessions";
import { messagesApi } from "@/lib/api/messages";
import { tasksApi } from "@/lib/api/tasks";

export const channelKeys = {
  all: ["channels"] as const,
  list: (filters?: ChannelFilters) => [...channelKeys.all, "list", filters] as const,
  detail: (id: string) => [...channelKeys.all, "detail", id] as const,
  groups: (channelId: string) => [...channelKeys.all, "groups", channelId] as const,
};

export const sessionKeys = {
  all: ["sessions"] as const,
  list: (groupId: string) => [...sessionKeys.all, "list", groupId] as const,
  detail: (id: string) => [...sessionKeys.all, "detail", id] as const,
};

export const messageKeys = {
  all: ["messages"] as const,
  list: (sessionId: string) => [...messageKeys.all, "list", sessionId] as const,
};

// Fetch channel list once - manual refresh available
export function useChannels(filters?: ChannelFilters) {
  return useQuery({
    queryKey: channelKeys.list(filters),
    queryFn: () => channelsApi.list(filters),
    staleTime: Infinity,
  });
}

export function useChannel(channelId: string | null) {
  return useQuery({
    queryKey: channelKeys.detail(channelId || ""),
    queryFn: () => channelsApi.get(channelId!),
    enabled: !!channelId,
    staleTime: Infinity,
  });
}

// Fetch groups for a channel
export function useChannelGroups(channelId: string | null) {
  return useQuery({
    queryKey: channelKeys.groups(channelId || ""),
    queryFn: () => channelsApi.getGroups(channelId!),
    enabled: !!channelId,
    staleTime: Infinity,
  });
}

// Fetch sessions for a group
export function useGroupSessions(groupId: string | null) {
  return useQuery({
    queryKey: sessionKeys.list(groupId || ""),
    queryFn: () => sessionsApi.listByGroup(groupId!),
    enabled: !!groupId,
    staleTime: Infinity,
  });
}

// Fetch a single session by ID (with task links and task titles)
export function useSession(sessionId: string | null) {
  return useQuery({
    queryKey: sessionKeys.detail(sessionId || ""),
    queryFn: async () => {
      const session = await sessionsApi.get(sessionId!);
      // Fetch task links separately since the endpoint doesn't include them
      try {
        const taskLinks = await sessionsApi.getTasksForSession(sessionId!);

        // Fetch task details to get titles
        const taskLinksWithTitles = await Promise.all(
          taskLinks.map(async (link) => {
            try {
              const task = await tasksApi.get(link.task_id);
              return {
                task_id: link.task_id,
                task_title: task.title,
                is_primary: link.is_primary,
                relationship_type: link.relationship_type,
              };
            } catch {
              return {
                task_id: link.task_id,
                task_title: null,
                is_primary: link.is_primary,
                relationship_type: link.relationship_type,
              };
            }
          })
        );

        return {
          ...session,
          task_links: taskLinksWithTitles,
        };
      } catch {
        // If fetching task links fails, return session without them
        return session;
      }
    },
    enabled: !!sessionId,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

// Fetch messages for a session - WebSocket handles new messages
export function useSessionMessages(sessionId: string | null) {
  return useQuery({
    queryKey: messageKeys.list(sessionId || ""),
    queryFn: () => messagesApi.listBySession(sessionId!),
    enabled: !!sessionId,
    staleTime: Infinity,
  });
}
