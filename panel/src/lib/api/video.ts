import api, { API_URL } from "./client";

// ---------------------------------------------------------------------------
// Video engine — held rendered-clip drafts (script + 9:16/1:1 MP4 + per-
// platform captions) the CEO previews, edits, and approves (posts to X /
// TikTok) or rejects in the panel. Nothing posts until an explicit approve;
// TikTok credentials are write-only (the API never returns the stored
// secrets). Mirrors lib/api/x.ts.
// ---------------------------------------------------------------------------

export type VideoCut = "vertical" | "square";

export interface VideoPost {
  task_id: string;
  source: string; // "video_post"
  title: string;
  status: string;
  occasion: string;
  script: string;
  platforms: string[]; // subset of "x" | "tiktok"
  x_caption?: string | null;
  tiktok_caption?: string | null;
  reject_reason?: string | null;
  mp4_paths?: Record<string, string>;
}

export interface VideoPostExecuteResult {
  status: string; // posted | posted_partial | post_failed | already_posted | ...
  posted: Record<string, string>; // platform -> posted id
  detail: string;
}

export interface VideoRequestResult {
  status: string; // "opened" | "disabled" | "not_opened"
  task_id?: string | null;
  detail: string;
}

export interface TikTokCredentialsStatus {
  has_credentials: boolean;
}

// GET /video/posts/{id}/media (roboco/api/routes/video.py) serves one
// rendered MP4 cut; VideoPost.mp4_paths (above) carries the server-side
// paths per cut. This builds that route's URL — but a native
// <video src> pointed straight at it 401s (a plain <video> GET carries none
// of axios's X-Agent-ID/X-Agent-Role headers), so the panel instead fetches
// the bytes via videoApi.getMediaBlob (axios) and drives <video> off an
// object URL. Kept for any direct-link use (e.g. opening the raw file).
export function videoMediaUrl(taskId: string, cut: VideoCut): string {
  return `${API_URL}/video/posts/${taskId}/media?cut=${cut}`;
}

export const videoApi = {
  listPosts: async (): Promise<VideoPost[]> => {
    const { data } = await api.get<VideoPost[]>("/video/posts");
    return data;
  },
  // Fetches the rendered cut as a Blob via axios (carrying the auth headers
  // a plain <video src> GET can't) so the caller can drive <video> off an
  // object URL instead of pointing it at the route directly.
  getMediaBlob: async (taskId: string, cut: VideoCut): Promise<Blob> => {
    const { data } = await api.get<Blob>(`/video/posts/${taskId}/media`, {
      params: { cut },
      responseType: "blob",
    });
    return data;
  },
  approve: async (
    taskId: string,
    captions?: { x_caption?: string; tiktok_caption?: string },
  ): Promise<VideoPostExecuteResult> => {
    const { data } = await api.post<VideoPostExecuteResult>(
      `/video/posts/${taskId}/approve`,
      captions ?? {},
    );
    return data;
  },
  reject: async (taskId: string, reason: string): Promise<VideoPost> => {
    const { data } = await api.post<VideoPost>(
      `/video/posts/${taskId}/reject`,
      { reason },
    );
    return data;
  },
  requestVideo: async (body: {
    occasion: string;
    brief: string;
    platforms: string[];
  }): Promise<VideoRequestResult> => {
    const { data } = await api.post<VideoRequestResult>("/video/request", body);
    return data;
  },
  getCredentialsStatus: async (): Promise<TikTokCredentialsStatus> => {
    const { data } = await api.get<TikTokCredentialsStatus>(
      "/tiktok/credentials",
    );
    return data;
  },
  setCredentials: async (creds: {
    client_key: string;
    client_secret: string;
    access_token: string;
    refresh_token: string;
  }): Promise<TikTokCredentialsStatus> => {
    const { data } = await api.post<TikTokCredentialsStatus>(
      "/tiktok/credentials",
      creds,
    );
    return data;
  },
};
