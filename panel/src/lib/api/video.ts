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

// NOTE: the committed `VideoPostResponse` (roboco/api/schemas/video.py)
// doesn't yet return the draft's `mp4_paths`, and no route serves the
// rendered bytes — the design doc (docs/internal/specs/2026-07-04-video-
// generation-remotion-design.md) calls for "a GET /video/posts/{id}/media
// route (nginx -> orchestrator)". This points at that route so the preview
// is wired correctly the moment the backend adds it; until then it 404s.
export function videoMediaUrl(taskId: string, cut: VideoCut): string {
  return `${API_URL}/video/posts/${taskId}/media?cut=${cut}`;
}

export const videoApi = {
  listPosts: async (): Promise<VideoPost[]> => {
    const { data } = await api.get<VideoPost[]>("/video/posts");
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
    const { data } = await api.post<VideoRequestResult>(
      "/video/request",
      body,
    );
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
