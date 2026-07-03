import api from "./client";

// Matches roboco.api.auth.routes.auth_status's response shape.
export interface AuthStatus {
  cloud_auth_enabled: boolean;
}

export const authApi = {
  // Always available regardless of the flag — the panel middleware and the
  // login page both probe this before deciding whether to gate/redirect.
  status: async (): Promise<AuthStatus> => {
    const { data } = await api.get<AuthStatus>("/auth/status");
    return data;
  },

  // FastAPI Users' cookie-login route expects an OAuth2 form body
  // (username/password), not JSON — the session cookie rides back on the
  // response, set by the browser automatically.
  login: async (email: string, password: string): Promise<void> => {
    const body = new URLSearchParams();
    body.set("username", email);
    body.set("password", password);
    await api.post("/auth/login", body, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
  },

  logout: async (): Promise<void> => {
    await api.post("/auth/logout");
  },
};
