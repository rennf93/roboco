import api from "./client";

// ---------------------------------------------------------------------------
// Entire trails bridge — the CEO's Entire token (write-only; the API never
// returns it), the bridge's status, and the retry of operations that stopped
// retrying. The bridge itself runs in the orchestrator.
// ---------------------------------------------------------------------------

export interface EntireTokenStatus {
  has_token: boolean;
}

export interface EntireTrailsStatus {
  enabled: boolean;
  has_token: boolean;
  cli_available: boolean;
  open_trails: number;
  pending: number;
  dead: number;
  last_error: string | null;
}

export const entireApi = {
  getTokenStatus: async (): Promise<EntireTokenStatus> => {
    const { data } = await api.get<EntireTokenStatus>("/entire/credentials");
    return data;
  },
  setToken: async (token: string): Promise<EntireTokenStatus> => {
    const { data } = await api.post<EntireTokenStatus>("/entire/credentials", {
      token,
    });
    return data;
  },
  getTrailsStatus: async (): Promise<EntireTrailsStatus> => {
    const { data } = await api.get<EntireTrailsStatus>("/entire/trails/status");
    return data;
  },
  retryTrails: async (): Promise<{ retried: number }> => {
    const { data } = await api.post<{ retried: number }>(
      "/entire/trails/retry",
    );
    return data;
  },
};
