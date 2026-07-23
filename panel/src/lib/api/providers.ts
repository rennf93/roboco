import api from "./client";
import type { AssignmentScope, ModelProvider } from "@/types";

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

export interface GrokKeyStatus {
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

export type RoutingMode =
  | "anthropic"
  | "grok"
  | "ollama"
  | "self_hosted"
  | "mix"
  | "cost_tiered";

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

// ---------------------------------------------------------------------------
// Complexity overrides (cost-tiered routing: compound ROLE(":"complexity) rows)
// ---------------------------------------------------------------------------

export type ComplexityLevel = "low" | "high";

/** One active ROLE(":"complexity) cost-tiered override row. */
export interface ComplexityOverride {
  role: string;
  complexity: ComplexityLevel;
  model_name: string;
  /** Set only on the PUT response, when the model crosses provider families
   * relative to the role's Anthropic baseline — allowed, never silent. */
  warning?: string | null;
}

/** Roles the complexity-override endpoint accepts a row for — mirrors the
 * server allowlist in api/routes/provider.py. Coordinator (cell_pm, main_pm),
 * pr_reviewer, and board/CEO-facing roles are never offered a row here; tier
 * pinning for those is deliberate — cell_pm especially, since the org's
 * documented weak-model incidents were precisely a cheap model on a PM role. */
export const COMPLEXITY_OVERRIDE_ROLES = ["developer", "qa", "documenter"] as const;

// ---------------------------------------------------------------------------
// Routing presets (named, full snapshots of the routing state)
// ---------------------------------------------------------------------------

/** One saved preset — list view (no payload). */
export interface RoutingPreset {
  id: string;
  name: string;
  created_at: string;
}

/** Result of applying a preset. */
export interface RoutingPresetApplyResult {
  mode: RoutingMode;
  assignments: ModelAssignment[];
  skipped: string[];
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

  getGrokKey: async (): Promise<GrokKeyStatus> => {
    const { data } = await api.get<GrokKeyStatus>("/providers/grok-key");
    return data;
  },

  setGrokKey: async (apiKey: string): Promise<GrokKeyStatus> => {
    const { data } = await api.put<GrokKeyStatus>("/providers/grok-key", {
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

  // -------------------------------------------------------------------------
  // Complexity overrides
  // -------------------------------------------------------------------------

  getComplexityOverrides: async (): Promise<ComplexityOverride[]> => {
    const { data } = await api.get<ComplexityOverride[]>(
      "/providers/complexity-overrides",
    );
    return data;
  },

  setComplexityOverride: async (
    payload: ComplexityOverride,
  ): Promise<ComplexityOverride> => {
    const { data } = await api.put<ComplexityOverride>(
      "/providers/complexity-overrides",
      payload,
    );
    return data;
  },

  deleteComplexityOverride: async (
    role: string,
    complexity: ComplexityLevel,
  ): Promise<void> => {
    await api.delete(
      `/providers/complexity-overrides/${encodeURIComponent(role)}/${complexity}`,
    );
  },

  // -------------------------------------------------------------------------
  // Routing presets
  // -------------------------------------------------------------------------

  listPresets: async (): Promise<RoutingPreset[]> => {
    const { data } = await api.get<RoutingPreset[]>("/providers/presets");
    return data;
  },

  savePreset: async (name: string): Promise<RoutingPreset> => {
    const { data } = await api.post<RoutingPreset>("/providers/presets", {
      name,
    });
    return data;
  },

  applyPreset: async (id: string): Promise<RoutingPresetApplyResult> => {
    const { data } = await api.post<RoutingPresetApplyResult>(
      `/providers/presets/${id}/apply`,
    );
    return data;
  },

  deletePreset: async (id: string): Promise<void> => {
    await api.delete(`/providers/presets/${id}`);
  },
};
