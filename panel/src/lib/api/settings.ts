import api from "./client";

// The CEO's panel display name — header user chip + Settings User Info card.
// Unset key (no row yet) falls back to this default; current behavior until
// the CEO edits it in Settings.
export const CEO_NAME_KEY = "ceo_name";
export const DEFAULT_CEO_NAME = "Renzo";

export interface SettingsResponse {
  settings: Record<string, string>;
}

export interface FeatureFlag {
  key: string;
  label: string;
  enabled: boolean;
}

export interface FeatureFlagsResponse {
  flags: FeatureFlag[];
  note: string;
}

export const settingsApi = {
  // GET /api/settings — all runtime-editable settings as a flat key→value map.
  getAll: async (): Promise<Record<string, string>> => {
    const { data } = await api.get<SettingsResponse>("/settings");
    return data.settings;
  },
  // PUT /api/settings/{key} — persist one setting; returns the full updated map.
  update: async (
    key: string,
    value: string,
  ): Promise<Record<string, string>> => {
    const { data } = await api.put<SettingsResponse>(`/settings/${key}`, {
      value,
    });
    return data.settings;
  },
  // GET /api/settings/feature-flags — effective flag values (override, else env).
  getFeatureFlags: async (): Promise<FeatureFlagsResponse> => {
    const { data } = await api.get<FeatureFlagsResponse>(
      "/settings/feature-flags",
    );
    return data;
  },
  // PUT /api/settings/{key} — persist a feature flag as "true"/"false".
  setFeatureFlag: async (key: string, enabled: boolean): Promise<void> => {
    await api.put<SettingsResponse>(`/settings/${key}`, {
      value: enabled ? "true" : "false",
    });
  },
};
