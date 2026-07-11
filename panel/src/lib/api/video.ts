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
  source_task_id?: string | null; // the authoring task this draft rendered from
  // The following mirror the shared `video_draft` marker's render idempotency
  // keys (same fields as VideoPipelineItem) — optional because a draft may
  // predate the backend exposing them on this response. Undefined reads as
  // "not stale" everywhere below, so the re-render control degrades safely.
  composition_id?: string | null;
  render_status?: string | null; // null | "rendered" | "failed"
}

// One in-flight source=video authoring task — GET /video/pipeline
// (roboco/api/routes/video.py). Spans claim through the render loop's
// retry/failure states; a rendered task drops out (it's visible via
// VideoPost/listPosts instead).
export interface VideoPipelineItem {
  task_id: string;
  title: string;
  occasion: string;
  status: string;
  pr_number: number | null;
  composition_id: string | null;
  render_status: string | null; // null (pending/retrying) | "rendered" | "failed"
  render_attempts: number;
  max_attempts: number;
  render_error: string | null;
}

export interface VideoPostExecuteResult {
  status: string; // posted | posted_partial | post_failed | already_posted | ...
  posted: Record<string, string>; // platform -> posted id
  detail: string;
}

// One acted-on draft (posted or rejected) — the CEO's history view.
export interface VideoPostHistoryEntry {
  task_id: string;
  source: string; // "video_post"
  title: string;
  status: string; // "completed" | "cancelled"
  occasion: string;
  script: string;
  platforms: string[];
  x_caption?: string | null;
  tiktok_caption?: string | null;
  reject_reason?: string | null;
  posted: Record<string, string>; // platform -> posted id
  acted_at: string;
  source_task_id?: string | null; // the authoring task this draft rendered from
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

// GET /video/preview/{task_id}/{file_path:path} (roboco/api/routes/video.py)
// serves a video-authoring task's composition HTML + sibling assets directly
// from the project's merged read-clone, with iframe-permitting headers — the
// panel's live composition preview embeds this URL as an <iframe src>
// directly (unlike the MP4 media route, no auth-header workaround needed).
export function compositionPreviewUrl(
  authoringTaskId: string,
  compositionId: string,
  cut: VideoCut,
): string {
  const filePath = `motion/compositions/${compositionId}/${cut}.html`;
  return `${API_URL}/video/preview/${authoringTaskId}/${filePath}`;
}

export const videoApi = {
  listPosts: async (): Promise<VideoPost[]> => {
    const { data } = await api.get<VideoPost[]>("/video/posts");
    return data;
  },
  // Every in-flight source=video authoring task — the pipeline strip's basis.
  listPipeline: async (): Promise<VideoPipelineItem[]> => {
    const { data } = await api.get<VideoPipelineItem[]>("/video/pipeline");
    return data;
  },
  // Posted or rejected drafts, newest-acted-first. Fixed default limit (50);
  // no "load more" — pass a higher limit if the panel ever needs it.
  listHistory: async (limit = 50): Promise<VideoPostHistoryEntry[]> => {
    const { data } = await api.get<VideoPostHistoryEntry[]>(
      "/video/posts/history",
      { params: { limit } },
    );
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
    project_id: string;
  }): Promise<VideoRequestResult> => {
    const { data } = await api.post<VideoRequestResult>("/video/request", body);
    return data;
  },
  // POST /video/pipeline/{task_id}/rerender (roboco/api/routes/video.py) —
  // clears the authoring task's render idempotency keys so the render loop
  // re-picks it up. taskId is the *authoring* task (VideoPost.source_task_id),
  // not the held draft's own task_id.
  rerender: async (authoringTaskId: string): Promise<void> => {
    await api.post(`/video/pipeline/${authoringTaskId}/rerender`);
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
