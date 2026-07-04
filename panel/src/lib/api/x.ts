import api from "./client";

// ---------------------------------------------------------------------------
// X (Twitter) engine — held release-post + mention-reply drafts the CEO edits
// and approves (posts) or rejects (cancels) in the panel. Nothing posts until
// an explicit approve; credentials are write-only (the API never returns
// the stored secrets).
// ---------------------------------------------------------------------------

export interface XMentionRef {
  id: string;
  author_id: string;
  text: string;
}

export interface XFeatureRef {
  slug: string;
  title: string;
}

export interface XPost {
  task_id: string;
  source: "x_post" | "x_reply" | "x_feature";
  title: string;
  status: string;
  body: string;
  char_count: number;
  release_version?: string | null;
  mention?: XMentionRef | null;
  feature?: XFeatureRef | null;
  reject_reason?: string | null;
}

export interface XPostExecuteResult {
  status: string;
  tweet_id?: string | null;
  detail: string;
}

export interface XCredentialsStatus {
  has_credentials: boolean;
}

export const xApi = {
  listPosts: async (): Promise<XPost[]> => {
    const { data } = await api.get<XPost[]>("/x/posts");
    return data;
  },
  approve: async (
    taskId: string,
    editedBody?: string,
  ): Promise<XPostExecuteResult> => {
    const { data } = await api.post<XPostExecuteResult>(
      `/x/posts/${taskId}/approve`,
      editedBody ? { edited_body: editedBody } : {},
    );
    return data;
  },
  reject: async (taskId: string, reason: string): Promise<XPost> => {
    const { data } = await api.post<XPost>(`/x/posts/${taskId}/reject`, {
      reason,
    });
    return data;
  },
  getCredentialsStatus: async (): Promise<XCredentialsStatus> => {
    const { data } = await api.get<XCredentialsStatus>("/x/credentials");
    return data;
  },
  setCredentials: async (creds: {
    api_key: string;
    api_secret: string;
    access_token: string;
    access_token_secret: string;
  }): Promise<XCredentialsStatus> => {
    const { data } = await api.post<XCredentialsStatus>(
      "/x/credentials",
      creds,
    );
    return data;
  },
};
