import api from "./client";

export interface SettingsResponse {
  settings: Record<string, string>;
}

export const settingsApi = {
  // GET /api/settings — all runtime-editable settings as a flat key→value map.
  getAll: async (): Promise<Record<string, string>> => {
    const { data } = await api.get<SettingsResponse>("/settings");
    return data.settings;
  },
  // PUT /api/settings/{key} — persist one setting; returns the full updated map.
  update: async (key: string, value: string): Promise<Record<string, string>> => {
    const { data } = await api.put<SettingsResponse>(`/settings/${key}`, { value });
    return data.settings;
  },
};
