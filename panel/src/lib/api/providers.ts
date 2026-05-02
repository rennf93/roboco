import api from "./client";
import type {
  AssignmentScope,
  ModelProvider,
} from "@/types";

// Matches the backend's CatalogEntryResponse.
export interface CatalogEntry {
  model_name: string;
  provider_type: ModelProvider;
  display_name: string;
}

export interface OllamaKeyStatus {
  has_key: boolean;
  enabled: boolean;
}

export interface ModelAssignment {
  id: string;
  scope: AssignmentScope;
  scope_value: string | null;
  provider_type: ModelProvider;
  model_name: string;
}

export type RoutingMode = "anthropic" | "ollama" | "mix";

export interface ModeSnapshot {
  mode: RoutingMode;
  assignments: ModelAssignment[];
}

export interface ApplyModePayload {
  mode: RoutingMode;
  default_model?: string;
  // Required when mode=mix: map of agent_slug → model_name.
  per_agent?: Record<string, string>;
}

export const providersApi = {
  catalog: async (): Promise<CatalogEntry[]> => {
    const { data } = await api.get<CatalogEntry[]>("/providers/catalog");
    return data;
  },

  getOllamaKey: async (): Promise<OllamaKeyStatus> => {
    const { data } = await api.get<OllamaKeyStatus>("/providers/ollama-key");
    return data;
  },

  setOllamaKey: async (apiKey: string): Promise<OllamaKeyStatus> => {
    const { data } = await api.put<OllamaKeyStatus>("/providers/ollama-key", {
      api_key: apiKey,
    });
    return data;
  },

  getMode: async (): Promise<ModeSnapshot> => {
    const { data } = await api.get<ModeSnapshot>("/providers");
    return data;
  },

  applyMode: async (payload: ApplyModePayload): Promise<ModeSnapshot> => {
    const { data } = await api.post<ModeSnapshot>("/providers", payload);
    return data;
  },
};
