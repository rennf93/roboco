/**
 * Groups API Client
 *
 * API functions for group management within channels.
 */

import api from "./client";
import { isMockMode } from "@/lib/mock-data";
import type { Group } from "@/types";

// =============================================================================
// Types
// =============================================================================

export interface GroupCreate {
  channel_id: string;
  name: string;
  hierarchy_level?: number;
}

// =============================================================================
// API Client
// =============================================================================

export const groupsApi = {
  /**
   * Create a new group within a channel
   */
  create: async (group: GroupCreate): Promise<Group> => {
    if (isMockMode()) {
      return {
        id: `group-${Date.now()}`,
        name: group.name,
        hierarchy_level: group.hierarchy_level ?? 0,
        is_active: true,
        total_messages: 0,
        active_session_id: null,
      };
    }
    const { data } = await api.post<Group>("/groups", group);
    return data;
  },

  /**
   * Get a group by ID
   */
  get: async (groupId: string): Promise<Group> => {
    if (isMockMode()) {
      return {
        id: groupId,
        name: "Mock Group",
        hierarchy_level: 0,
        is_active: true,
        total_messages: 0,
        active_session_id: null,
      };
    }
    const { data } = await api.get<Group>(`/groups/${groupId}`);
    return data;
  },
};
