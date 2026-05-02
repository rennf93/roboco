/**
 * Agents API Client
 *
 * Fetches agent data from the backend API.
 */

import api from "./client";
import type { AgentRole, Team } from "@/types";
import { isMockMode, mockAgents } from "@/lib/mock-data";

export interface AgentDefinition {
  id: string; // slug from backend (e.g., "be-dev-1")
  name: string;
  role: AgentRole | null;
  team: Team | null;
}

interface AgentApiResponse {
  id: string; // UUID (not used for definitions)
  name: string;
  slug: string;
  role: string;
  team: string | null;
}

export const agentsApi = {
  /**
   * Get all agents from the API
   */
  getAll: async (): Promise<AgentDefinition[]> => {
    if (isMockMode()) {
      return mockAgents.map((a) => ({
        id: a.slug || a.id,
        name: a.name,
        role: a.role,
        team: a.team,
      }));
    }

    const response = await api.get<AgentApiResponse[]>("/agents");
    const data = response.data;

    // Handle unexpected response formats
    if (!data || !Array.isArray(data)) {
      console.warn("Unexpected agents API response:", data);
      return [];
    }

    return data.map((a) => ({
      id: a.slug || a.id, // Use slug as ID, fallback to UUID
      name: a.name || "Unknown",
      role: (a.role as AgentRole) || null,
      team: (a.team as Team) || null,
    }));
  },

  /**
   * Get a single agent by ID or slug
   */
  getOne: async (idOrSlug: string): Promise<AgentDefinition> => {
    const response = await api.get<AgentApiResponse>(`/agents/${idOrSlug}`);
    const a = response.data;
    return {
      id: a.slug,
      name: a.name,
      role: a.role as AgentRole,
      team: a.team as Team | null,
    };
  },
};
