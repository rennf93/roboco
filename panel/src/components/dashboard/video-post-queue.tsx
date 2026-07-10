"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { videoApi } from "@/lib/api";
import { compositionPreviewUrl } from "@/lib/api/video";
import type {
  VideoCut,
  VideoPost,
  VideoPostExecuteResult,
} from "@/lib/api/video";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ProjectSelector } from "@/components/projects/project-selector";
import { useProjects } from "@/hooks/use-projects";
import { CheckCircle2, Film, RefreshCw, Sparkles, XCircle } from "lucide-react";
import { toast } from "sonner";

const MAX_X_CAPTION_CHARS = 280;
const MAX_TIKTOK_CAPTION_CHARS = 2200;
const _MIN_REASON_CHARS = 4;

const PLATFORM_LABELS: Record<string, string> = { x: "X", tiktok: "TikTok" };
const REQUEST_PLATFORMS = ["x", "tiktok"] as const;

// Only one source reaches this queue today; a function (not a literal)
// mirrors XPostQueue's sourceMeta pattern and costs nothing to extend later.
function sourceMeta() {
  return { label: "Video", icon: Film };
}

function describeExecuteResult(result: VideoPostExecuteResult): string {
  if (result.status === "posted") return "Posted to all platforms.";
  if (result.status === "posted_partial")
    return `Posted to some platforms — ${result.detail}`;
  if (result.status === "post_failed")
    return `Posting failed: ${result.detail}`;
  if (result.status === "already_posted") return "Already posted — no-op.";
  if (result.status === "already_in_progress")
    return "A post is already in progress for this draft.";
  if (result.status === "no_platforms")
    return "This draft has no target platforms.";
  if (result.status === "lock_lost")
    return "The post lock was lost mid-upload — retry the approve.";
  if (result.status === "redis_unavailable")
    return "Redis is unavailable — can't acquire the post lock.";
  return `${result.status}: ${result.detail}`;
}

// Re-render retry control — only rendered by the caller when the draft's
// render is stale (render_status === "failed"; see VideoPostRow). Three
// visual states: idle (button, ready to click), loading (mutation
// in-flight), error (the retry itself failed — button re-enables so the CEO
// can try again). Mirrors the pipeline strip's render_failed derivation in
// video-pipeline-utils.ts.
function RerenderControl({ authoringTaskId }: { authoringTaskId: string }) {
  const queryClient = useQueryClient();
  const rerenderMutation = useMutation({
    mutationFn: () => videoApi.rerender(authoringTaskId),
    onSuccess: () => {
      toast.success("Re-render queued — it will re-pick up on the next cycle.");
      queryClient.invalidateQueries({ queryKey: ["video", "pipeline"] });
    },
    onError: (e) =>
      toast.error(
        `Re-render failed: ${e instanceof Error ? e.message : "error"}`,
      ),
  });

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={rerenderMutation.isPending}
      onClick={() => rerenderMutation.mutate()}
      className={
        rerenderMutation.isError
          ? "border-destructive text-destructive"
          : undefined
      }
    >
      <RefreshCw
        className={`mr-1 h-4 w-4 ${rerenderMutation.isPending ? "animate-spin" : ""}`}
      />
      {rerenderMutation.isPending
        ? "Re-rendering..."
        : rerenderMutation.isError
          ? "Retry re-render"
          : "Re-render"}
    </Button>
  );
}

// Live composition preview: the authoring task's actual HyperFrames HTML for
// the currently-selected cut, embedded via the backend's composition-HTML
// proxy (iframe-permitting headers, so a direct <iframe src> works — no
// blob-fetch workaround needed, unlike the MP4 player below). Read-only
// captions sit alongside so the CEO can compare the live composition against
// what will actually post, before approving. Renders nothing when the draft
// carries no composition_id (older drafts / backend not yet exposing it).
function CompositionPreviewPanel({
  post,
  cut,
}: {
  post: VideoPost;
  cut: VideoCut;
}) {
  if (!post.composition_id || !post.source_task_id) return null;
  return (
    <div className="mb-3 grid gap-3 rounded-md border p-3 sm:grid-cols-2">
      <iframe
        src={compositionPreviewUrl(
          post.source_task_id,
          post.composition_id,
          cut,
        )}
        title={`${post.title} — live composition preview`}
        sandbox="allow-scripts"
        loading="lazy"
        className="aspect-video w-full rounded-md border bg-black"
      />
      <div className="space-y-2 text-sm">
        <p className="font-medium text-muted-foreground">
          Captions as they will post
        </p>
        {post.x_caption && (
          <p>
            <span className="font-medium">X:</span> {post.x_caption}
          </p>
        )}
        {post.tiktok_caption && (
          <p>
            <span className="font-medium">TikTok:</span> {post.tiktok_caption}
          </p>
        )}
      </div>
    </div>
  );
}

// One row of the queue: an MP4 preview (9:16 / 1:1 cut switcher) + per-
// platform editable captions + approve/reject. Mirrors XPostRow. Unchecking
// a platform's "Edit ... caption" box leaves it disabled (shown, not sent) —
// approve always posts every platform already in the draft; the checkbox
// only controls whether YOUR edit overrides that platform's stored caption.
function VideoPostRow({
  post,
  onApprove,
  onReject,
  approving,
}: {
  post: VideoPost;
  onApprove: (
    taskId: string,
    captions: { x_caption?: string; tiktok_caption?: string },
  ) => void;
  onReject: (post: VideoPost) => void;
  approving: boolean;
}) {
  // Default to whichever cut actually rendered — a missing vertical cut
  // (mp4_paths lacks the key) must not open on a guaranteed-blank player.
  const [cut, setCut] = useState<VideoCut>(() =>
    post.mp4_paths?.vertical ? "vertical" : "square",
  );
  const [editX, setEditX] = useState(post.platforms.includes("x"));
  const [editTiktok, setEditTiktok] = useState(
    post.platforms.includes("tiktok"),
  );
  // `edited*` holds the CEO's in-progress textarea input; null means "show
  // the server value". Deriving the displayed caption per render avoids
  // copying the refetched prop into local state once (mirrors XPostRow).
  const [editedX, setEditedX] = useState<string | null>(null);
  const [editedTiktok, setEditedTiktok] = useState<string | null>(null);
  const xCaption = editedX ?? post.x_caption ?? "";
  const tiktokCaption = editedTiktok ?? post.tiktok_caption ?? "";
  const [videoSrc, setVideoSrc] = useState<string | null>(null);
  const meta = sourceMeta();

  // A native <video src> GET doesn't carry axios's auth headers, so fetch
  // the cut as a Blob (through axios) and drive <video> off an object URL
  // instead. Re-fetches on cut change; always revokes the previous URL.
  // Skips the fetch entirely when mp4_paths has no entry for this cut — the
  // JSX below never renders <video> in that case (falls back to a missing-
  // cut placeholder instead), so a stale videoSrc is simply never read.
  useEffect(() => {
    if (!post.mp4_paths?.[cut]) {
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    videoApi
      .getMediaBlob(post.task_id, cut)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setVideoSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setVideoSrc(null);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [post.task_id, cut, post.mp4_paths]);

  const xOverLimit = editX && xCaption.length > MAX_X_CAPTION_CHARS;
  const tiktokOverLimit =
    editTiktok && tiktokCaption.length > MAX_TIKTOK_CAPTION_CHARS;
  const overLimit = xOverLimit || tiktokOverLimit;
  // The re-render (CEO retry) endpoint's use case is retrying a render that
  // hit a terminal failed state — mirrors video-pipeline-utils.ts's
  // render_failed derivation. Undefined render_status (backend not yet
  // exposing it on this response, or a healthy render) never shows the button.
  const isStale = post.render_status === "failed" && !!post.source_task_id;

  const handleApprove = () => {
    onApprove(post.task_id, {
      ...(editX ? { x_caption: xCaption } : {}),
      ...(editTiktok ? { tiktok_caption: tiktokCaption } : {}),
    });
  };

  return (
    <div className="rounded-lg border p-4 transition-colors hover:bg-muted/50">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <meta.icon className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">{meta.label}</span>
        {post.occasion && <Badge variant="outline">{post.occasion}</Badge>}
        {isStale && post.source_task_id && (
          <div className="ml-auto">
            <RerenderControl authoringTaskId={post.source_task_id} />
          </div>
        )}
      </div>

      <p className="mb-1 text-sm font-medium">{post.title}</p>
      {post.script && (
        <p className="mb-3 line-clamp-2 text-sm text-muted-foreground">
          {post.script}
        </p>
      )}

      <CompositionPreviewPanel post={post} cut={cut} />

      <div className="mb-3 space-y-2">
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant={cut === "vertical" ? "default" : "outline"}
            disabled={!post.mp4_paths?.vertical}
            title={
              post.mp4_paths?.vertical ? undefined : "9:16 hasn't rendered yet"
            }
            onClick={() => setCut("vertical")}
          >
            9:16{!post.mp4_paths?.vertical && " (missing)"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant={cut === "square" ? "default" : "outline"}
            disabled={!post.mp4_paths?.square}
            title={
              post.mp4_paths?.square ? undefined : "1:1 hasn't rendered yet"
            }
            onClick={() => setCut("square")}
          >
            1:1{!post.mp4_paths?.square && " (missing)"}
          </Button>
        </div>
        {post.mp4_paths?.[cut] ? (
          <video
            key={`${post.task_id}-${cut}`}
            controls
            className="mx-auto max-h-96 w-full rounded-md bg-black object-contain"
            src={videoSrc ?? undefined}
          >
            Your browser does not support embedded video.
          </video>
        ) : (
          <div className="flex h-48 items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
            This cut hasn&apos;t rendered yet
          </div>
        )}
      </div>

      <div className="space-y-3">
        {post.platforms.includes("x") && (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Checkbox
                id={`${post.task_id}-x-edit`}
                checked={editX}
                onCheckedChange={(c) => setEditX(c === true)}
              />
              <Label htmlFor={`${post.task_id}-x-edit`} className="text-sm">
                Edit X caption
              </Label>
            </div>
            <Textarea
              value={xCaption}
              onChange={(e) => setEditedX(e.target.value)}
              disabled={!editX}
              rows={2}
              className={xOverLimit ? "border-destructive" : undefined}
            />
            <p
              className={`text-right text-xs ${xOverLimit ? "text-destructive" : "text-muted-foreground"}`}
            >
              {xCaption.length}/{MAX_X_CAPTION_CHARS}
            </p>
          </div>
        )}

        {post.platforms.includes("tiktok") && (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Checkbox
                id={`${post.task_id}-tiktok-edit`}
                checked={editTiktok}
                onCheckedChange={(c) => setEditTiktok(c === true)}
              />
              <Label
                htmlFor={`${post.task_id}-tiktok-edit`}
                className="text-sm"
              >
                Edit TikTok caption
              </Label>
            </div>
            <Textarea
              value={tiktokCaption}
              onChange={(e) => setEditedTiktok(e.target.value)}
              disabled={!editTiktok}
              rows={2}
              className={tiktokOverLimit ? "border-destructive" : undefined}
            />
            <p
              className={`text-right text-xs ${tiktokOverLimit ? "text-destructive" : "text-muted-foreground"}`}
            >
              {tiktokCaption.length}/{MAX_TIKTOK_CAPTION_CHARS}
            </p>
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
        <Button
          variant="outline"
          size="sm"
          className="text-destructive hover:text-destructive"
          onClick={() => onReject(post)}
        >
          <XCircle className="mr-1 h-4 w-4" />
          Reject
        </Button>
        <Button
          size="sm"
          className="bg-green-600 hover:bg-green-700"
          disabled={approving || overLimit}
          onClick={handleApprove}
        >
          <CheckCircle2 className="mr-1 h-4 w-4" />
          Approve &amp; post
        </Button>
      </div>
    </div>
  );
}

// On-demand "Request a video" dialog: occasion + brief + platforms ->
// POST /video/request. No X equivalent — video is the only engine with an
// on-demand trigger — so this is new, not mirrored.
function RequestVideoDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [occasion, setOccasion] = useState("");
  const [brief, setBrief] = useState("");
  const [platforms, setPlatforms] = useState<string[]>(["x", "tiktok"]);
  // null means "no explicit pick yet" — derived below to the current (first)
  // video-enabled project, mirroring the caption-tracking pattern elsewhere
  // in this file (`editedX ?? post.x_caption`) rather than syncing via effect.
  const [projectId, setProjectId] = useState<string | null>(null);
  const { data: allProjects = [] } = useProjects();
  const videoProjects = allProjects.filter((p) => p.video_engine_enabled);
  const hasVideoProjects = videoProjects.length > 0;
  const effectiveProjectId = projectId ?? videoProjects[0]?.id ?? null;

  const requestMutation = useMutation({
    mutationFn: () =>
      videoApi.requestVideo({
        occasion: occasion.trim(),
        brief: brief.trim(),
        platforms,
        project_id: effectiveProjectId as string,
      }),
    onSuccess: (result) => {
      if (result.status === "opened") {
        toast.success(result.detail);
        onOpenChange(false);
        setOccasion("");
        setBrief("");
        setPlatforms(["x", "tiktok"]);
        setProjectId(null);
      } else {
        toast.warning(result.detail);
      }
    },
    onError: (e) =>
      toast.error(
        `Request failed: ${e instanceof Error ? e.message : "error"}`,
      ),
  });

  const togglePlatform = (platform: string) => {
    setPlatforms((prev) =>
      prev.includes(platform)
        ? prev.filter((p) => p !== platform)
        : [...prev, platform],
    );
  };

  const canSubmit =
    !!effectiveProjectId &&
    occasion.trim().length > 0 &&
    brief.trim().length > 0 &&
    platforms.length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Request a video</DialogTitle>
          <DialogDescription>
            Opens a video-authoring task for a UX/UI dev — it rides the normal
            delivery flow and the rendered clip lands back in this queue once
            rendering finishes.
          </DialogDescription>
        </DialogHeader>
        {hasVideoProjects ? (
          <>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Project</Label>
                <ProjectSelector
                  value={effectiveProjectId}
                  onChange={setProjectId}
                  placeholder="Select the project this video is about..."
                  allowClear={false}
                  videoEngineOnly
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="video-request-occasion">Occasion</Label>
                <Input
                  id="video-request-occasion"
                  placeholder="e.g. v0.19.0 launch, Founder's Day..."
                  value={occasion}
                  onChange={(e) => setOccasion(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="video-request-brief">Brief</Label>
                <Textarea
                  id="video-request-brief"
                  placeholder="What should this video cover?"
                  value={brief}
                  onChange={(e) => setBrief(e.target.value)}
                  rows={4}
                />
              </div>
              <div className="space-y-2">
                <Label>Platforms</Label>
                <div className="flex gap-4">
                  {REQUEST_PLATFORMS.map((platform) => (
                    <div key={platform} className="flex items-center gap-2">
                      <Checkbox
                        id={`video-request-${platform}`}
                        checked={platforms.includes(platform)}
                        onCheckedChange={() => togglePlatform(platform)}
                      />
                      <Label
                        htmlFor={`video-request-${platform}`}
                        className="text-sm font-normal"
                      >
                        {PLATFORM_LABELS[platform]}
                      </Label>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                onClick={() => requestMutation.mutate()}
                disabled={!canSubmit || requestMutation.isPending}
              >
                {requestMutation.isPending ? "Requesting..." : "Request"}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              No projects have the video engine enabled yet. Turn it on for a
              project in its edit dialog (Projects → Edit) before requesting a
              video.
            </p>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

// CEO queue for held video_post drafts (rendered clips from release/
// spotlight/on-demand triggers). Hidden while loading; shows an empty-state
// card (with the on-demand request action) when there are no drafts yet —
// mirrors XPostQueue. Posted/rejected drafts move to the unified history on
// /social (SocialHistorySection).
export function VideoPostQueue({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState<VideoPost | null>(null);
  const [reason, setReason] = useState("");
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [requestOpen, setRequestOpen] = useState(false);

  const { data: posts, isLoading } = useQuery({
    queryKey: ["video", "posts"],
    queryFn: () => videoApi.listPosts(),
    refetchInterval: 30000,
  });
  // Only consulted for the empty-state copy below — tells apart "nothing
  // rendered yet, but N videos are moving through the pipeline" from a
  // truly idle engine. Same 30s cadence as the queue + the pipeline strip.
  const { data: pipeline } = useQuery({
    queryKey: ["video", "pipeline"],
    queryFn: () => videoApi.listPipeline(),
    refetchInterval: 30000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["video", "posts"] });

  const approveMutation = useMutation({
    mutationFn: ({
      taskId,
      captions,
    }: {
      taskId: string;
      captions: { x_caption?: string; tiktok_caption?: string };
    }) => videoApi.approve(taskId, captions),
    onSuccess: (result) => {
      invalidate();
      if (result.status === "posted") {
        toast.success(describeExecuteResult(result));
      } else {
        toast.warning(describeExecuteResult(result));
      }
    },
    onError: (e) =>
      toast.error(
        `Approve failed: ${e instanceof Error ? e.message : "error"}`,
      ),
    onSettled: () => setApprovingId(null),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ taskId, reason }: { taskId: string; reason: string }) =>
      videoApi.reject(taskId, reason),
    onSuccess: () => {
      invalidate();
      toast.success("Draft rejected");
      closeReject();
    },
    onError: (e) =>
      toast.error(`Reject failed: ${e instanceof Error ? e.message : "error"}`),
  });

  const closeReject = () => {
    setRejecting(null);
    setReason("");
  };

  const confirmReject = () => {
    if (!rejecting) return;
    if (reason.trim().length < _MIN_REASON_CHARS) {
      toast.error("Give a brief reason for rejecting");
      return;
    }
    rejectMutation.mutate({ taskId: rejecting.task_id, reason: reason.trim() });
  };

  const handleApprove = (
    taskId: string,
    captions: { x_caption?: string; tiktok_caption?: string },
  ) => {
    setApprovingId(taskId);
    approveMutation.mutate({ taskId, captions });
  };

  if (isLoading) return null;

  const requestButton = (
    <Button variant="outline" size="sm" onClick={() => setRequestOpen(true)}>
      <Sparkles className="mr-1 h-4 w-4" />
      Request a video
    </Button>
  );

  if (!posts || posts.length === 0) {
    return (
      <>
        <Card className={className}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Film className="h-5 w-5" />
              Video Post Queue
            </CardTitle>
            <CardAction>{requestButton}</CardAction>
            <CardDescription>
              Rendered clips from a release, a feature spotlight, or a request
              below land here for you to preview, edit captions, approve, or
              reject. Nothing posts on its own.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              {pipeline && pipeline.length > 0
                ? `${pipeline.length} video${pipeline.length === 1 ? "" : "s"} in flight — nothing rendered yet. Check the pipeline above for status.`
                : "No drafts yet. Set your keys in Settings → X (Twitter) / TikTok Credentials and enable the video engine — or request one on demand above."}
            </p>
          </CardContent>
        </Card>
        <RequestVideoDialog open={requestOpen} onOpenChange={setRequestOpen} />
      </>
    );
  }

  return (
    <>
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Film className="h-5 w-5" />
            Video Post Queue
            <Badge variant="secondary">{posts.length}</Badge>
          </CardTitle>
          <CardAction>{requestButton}</CardAction>
          <CardDescription>
            Rendered clips — preview both cuts, edit captions, approve (posts to
            the target platforms), or reject. Nothing posts on its own.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {posts.map((post) => (
            <VideoPostRow
              key={post.task_id}
              post={post}
              onApprove={handleApprove}
              onReject={setRejecting}
              approving={approvingId === post.task_id}
            />
          ))}
        </CardContent>
      </Card>

      <Dialog open={!!rejecting} onOpenChange={() => closeReject()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject draft</DialogTitle>
            <DialogDescription>
              This cancels the draft — it will not be posted. Give a brief
              reason (it is recorded).
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="video-reject-reason">Reason</Label>
            <Textarea
              id="video-reject-reason"
              placeholder="e.g. off-brand tone, wrong occasion..."
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeReject}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmReject}
              disabled={rejectMutation.isPending}
            >
              {rejectMutation.isPending ? "Rejecting..." : "Reject"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <RequestVideoDialog open={requestOpen} onOpenChange={setRequestOpen} />
    </>
  );
}
