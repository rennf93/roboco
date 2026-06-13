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

export type RoutingMode = "anthropic" | "ollama" | "self_hosted" | "mix";

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

// ---------------------------------------------------------------------------
// Self-hosted LLM types
// ---------------------------------------------------------------------------

/** Configuration stored server-side for the self-hosted provider. */
export interface SelfHostedConfig {
  base_url: string | null;
  has_token: boolean;
  enabled: boolean;
}

/** Result of a test-connection call. */
export interface SelfHostedTestResult {
  ok: boolean;
  model_count: number | null;
  error: string | null;
}

/** One model entry returned by the discovery endpoint. */
export interface SelfHostedModel {
  model_name: string;
  display_name: string;
}

/** Payload for saving the self-hosted config (base URL + optional token). */
export interface SelfHostedConfigPayload {
  base_url: string;
  auth_token?: string; // omit to leave token unchanged; "" to clear
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

  // -------------------------------------------------------------------------
  // Self-hosted provider
  // -------------------------------------------------------------------------

  getSelfHostedConfig: async (): Promise<SelfHostedConfig> => {
    const { data } = await api.get<SelfHostedConfig>("/providers/self-hosted");
    return data;
  },

  saveSelfHostedConfig: async (
    payload: SelfHostedConfigPayload,
  ): Promise<SelfHostedConfig> => {
    const { data } = await api.put<SelfHostedConfig>(
      "/providers/self-hosted",
      payload,
    );
    return data;
  },

  testSelfHosted: async (): Promise<SelfHostedTestResult> => {
    const { data } = await api.post<SelfHostedTestResult>(
      "/providers/self-hosted/test",
    );
    return data;
  },

  getSelfHostedModels: async (): Promise<SelfHostedModel[]> => {
    const { data } = await api.get<SelfHostedModel[]>(
      "/providers/self-hosted/models",
    );
    return data;
  },
};
