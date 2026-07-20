import api from "./client";

// ---------------------------------------------------------------------------
// GitHub App integration — the CEO's App id + private key credentials
// (write-only; the API never returns the stored key) plus the "Select repo"
// picker's installation/repository listing.
// ---------------------------------------------------------------------------

export interface GitHubAppCredentialsStatus {
  has_credentials: boolean;
}

export interface GitHubAppInstallation {
  id: number;
  account_login: string;
}

export interface GitHubAppInstallationRepository {
  full_name: string;
  clone_url: string;
  private: boolean;
}

export const githubAppApi = {
  getCredentialsStatus: async (): Promise<GitHubAppCredentialsStatus> => {
    const { data } = await api.get<GitHubAppCredentialsStatus>(
      "/github-app/credentials",
    );
    return data;
  },
  setCredentials: async (creds: {
    app_id: string;
    private_key: string;
  }): Promise<GitHubAppCredentialsStatus> => {
    const { data } = await api.put<GitHubAppCredentialsStatus>(
      "/github-app/credentials",
      creds,
    );
    return data;
  },
  clearCredentials: async (): Promise<GitHubAppCredentialsStatus> => {
    const { data } = await api.delete<GitHubAppCredentialsStatus>(
      "/github-app/credentials",
    );
    return data;
  },
  listInstallations: async (): Promise<GitHubAppInstallation[]> => {
    const { data } = await api.get<GitHubAppInstallation[]>(
      "/github-app/installations",
    );
    return data;
  },
  listInstallationRepositories: async (
    installationId: number,
  ): Promise<GitHubAppInstallationRepository[]> => {
    const { data } = await api.get<GitHubAppInstallationRepository[]>(
      `/github-app/installations/${installationId}/repositories`,
    );
    return data;
  },
};
