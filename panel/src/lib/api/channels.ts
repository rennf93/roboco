import api from "./client";
import type { Channel, PaginatedResponse, Group } from "@/types";
import { ChannelType } from "@/types";
import { isMockMode, mockChannels, mockGroups } from "@/lib/mock-data";

export interface ChannelFilters {
  type?: string;
  is_private?: boolean;
}

export interface ChannelCreate {
  name: string;
  slug: string;
  type?: string;
  description?: string;
  topic?: string;
  is_private?: boolean;
}

export interface ChannelUpdate {
  name?: string;
  description?: string;
  topic?: string;
  is_archived?: boolean;
}

export const channelsApi = {
  // List all channels
  list: async (filters?: ChannelFilters): Promise<Channel[]> => {
    if (isMockMode()) {
      let channels = [...mockChannels] as Channel[];
      if (filters?.type) {
        channels = channels.filter((c) => c.type === filters.type);
      }
      if (filters?.is_private !== undefined) {
        channels = channels.filter((c) => c.is_private === filters.is_private);
      }
      return channels;
    }
    const { data } = await api.get<PaginatedResponse<Channel>>("/channels", { params: filters });
    return data.items;
  },

  // Get channel by ID
  get: async (channelId: string): Promise<Channel> => {
    if (isMockMode()) {
      const channel = mockChannels.find((c) => c.id === channelId);
      if (channel) return channel as Channel;
      throw new Error("Channel not found");
    }
    const { data } = await api.get<Channel>("/channels/" + channelId);
    return data;
  },

  // Get channel by slug
  getBySlug: async (slug: string): Promise<Channel> => {
    if (isMockMode()) {
      const channel = mockChannels.find((c) => c.slug === slug);
      if (channel) return channel as Channel;
      throw new Error("Channel not found");
    }
    // Backend uses query param filter, not path segment
    const { data } = await api.get<PaginatedResponse<Channel>>("/channels", {
      params: { slug },
    });
    if (!data.items.length) {
      throw new Error("Channel not found");
    }
    return data.items[0];
  },

  // Get groups for a channel
  getGroups: async (channelId: string): Promise<Group[]> => {
    if (isMockMode()) {
      return mockGroups as Group[];
    }
    const { data } = await api.get<Group[]>("/channels/" + channelId + "/groups");
    return data;
  },

  // Create a new channel (PM/CEO only)
  create: async (channel: ChannelCreate): Promise<Channel> => {
    if (isMockMode()) {
      const newChannel: Channel = {
        id: `channel-${Date.now()}`,
        name: channel.name,
        slug: channel.slug,
        type: (channel.type as ChannelType) || ChannelType.CELL,
        description: channel.description || null,
        topic: channel.topic || null,
        is_private: channel.is_private || false,
        is_archived: false,
        member_count: 0,
        message_count: 0,
        group_count: 0,
        can_write: true,
      };
      (mockChannels as Channel[]).push(newChannel);
      return newChannel;
    }
    const { data } = await api.post<Channel>("/channels", channel);
    return data;
  },

  // Update a channel (PM/CEO only)
  update: async (channelId: string, updates: ChannelUpdate): Promise<Channel> => {
    if (isMockMode()) {
      const idx = mockChannels.findIndex((c) => c.id === channelId);
      if (idx === -1) throw new Error("Channel not found");
      const updated = { ...mockChannels[idx], ...updates } as Channel;
      (mockChannels as Channel[])[idx] = updated;
      return updated;
    }
    const { data } = await api.patch<Channel>("/channels/" + channelId, updates);
    return data;
  },

  // Add a member to a channel (PM/CEO only)
  addMember: async (channelId: string, agentId: string): Promise<void> => {
    if (isMockMode()) {
      return;
    }
    await api.post("/channels/" + channelId + "/add-member", { agent_id: agentId });
  },

  // Remove a member from a channel (PM/CEO only)
  removeMember: async (channelId: string, agentId: string): Promise<void> => {
    if (isMockMode()) {
      return;
    }
    await api.delete("/channels/" + channelId + "/remove-member", {
      data: { agent_id: agentId },
    });
  },
};
