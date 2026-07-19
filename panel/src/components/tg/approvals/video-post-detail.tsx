"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { videoApi, type VideoCut, type VideoPost } from "@/lib/api/video";
import { getErrorMessage } from "@/lib/api/client";
import { haptics } from "@/lib/telegram/webapp";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { PrimaryAction } from "./primary-action";
import { RejectForm } from "./reject-form";
import { cn } from "@/lib/utils";

const MAX_X_CAPTION_CHARS = 280;
const MAX_TIKTOK_CAPTION_CHARS = 2200;
const MIN_REJECT_CHARS = 4;

const CUT_LABELS: Record<VideoCut, string> = {
  vertical: "9:16",
  square: "1:1",
};

/**
 * Rendered-cut player. The media route is authenticated, so a bare
 * `<video src>` would 401 — fetch the MP4 as a blob through the authed
 * client and play the object URL (the desktop queue's exact pattern).
 */
function CutPlayer({ post }: { post: VideoPost }) {
  const paths = post.mp4_paths ?? {};
  const [cut, setCut] = useState<VideoCut>(paths.vertical ? "vertical" : "square");
  // url === null means the fetch for that cut failed; a stale entry for a
  // different cut is simply ignored in render, so no synchronous state
  // reset is needed when the cut changes.
  const [loaded, setLoaded] = useState<{
    cut: VideoCut;
    url: string | null;
  } | null>(null);
  // A stable string dep — depending on the paths object would re-run the
  // effect (and refetch the MP4) on every render.
  const mediaPath = paths[cut];

  useEffect(() => {
    if (!mediaPath) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    void videoApi
      .getMediaBlob(post.task_id, cut)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setLoaded({ cut, url: objectUrl });
      })
      .catch(() => {
        if (!cancelled) setLoaded({ cut, url: null });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [post.task_id, cut, mediaPath]);

  const src = loaded?.cut === cut ? loaded.url : null;
  const failed = loaded?.cut === cut && loaded.url === null;

  return (
    <div className="space-y-2">
      <div className="flex gap-1.5">
        {(Object.keys(CUT_LABELS) as VideoCut[]).map((c) => (
          <Button
            key={c}
            size="sm"
            variant={cut === c ? "default" : "outline"}
            disabled={!paths[c]}
            onClick={() => setCut(c)}
          >
            {CUT_LABELS[c]}
            {!paths[c] && " (missing)"}
          </Button>
        ))}
      </div>
      {src ? (
        <video
          src={src}
          controls
          playsInline
          className={cn(
            "w-full rounded-md bg-black",
            cut === "vertical" ? "aspect-[9/16] max-h-[60dvh]" : "aspect-square",
          )}
        />
      ) : (
        <div className="flex aspect-video items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground">
          {!mediaPath
            ? "This cut wasn't rendered"
            : failed
              ? "Preview failed to load"
              : "Loading preview…"}
        </div>
      )}
    </div>
  );
}

function CaptionEditor({
  platform,
  stored,
  maxChars,
  value,
  onChange,
}: {
  platform: string;
  stored: string;
  maxChars: number;
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  const editing = value !== null;
  const caption = value ?? stored;
  const overLimit = caption.length > maxChars;
  return (
    <div className="space-y-1">
      <label className="flex items-center gap-2 text-xs font-medium">
        <input
          type="checkbox"
          checked={editing}
          onChange={(e) => onChange(e.target.checked ? stored : null)}
        />
        Edit {platform} caption
      </label>
      <Textarea
        value={caption}
        disabled={!editing}
        onChange={(e) => onChange(e.target.value)}
        rows={3}
        className={cn("text-sm", editing && overLimit && "border-destructive")}
      />
      <p
        className={cn(
          "text-right text-[11px] tabular-nums",
          editing && overLimit ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {caption.length}/{maxChars}
      </p>
    </div>
  );
}

/** Focused video draft: cut-toggled player, per-platform caption edits,
 * then post (X + TikTok as configured) or reject. */
export function VideoPostDetail({
  post,
  onDone,
}: {
  post: VideoPost;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [xCaption, setXCaption] = useState<string | null>(null);
  const [tiktokCaption, setTiktokCaption] = useState<string | null>(null);

  const overLimit =
    (xCaption !== null && xCaption.length > MAX_X_CAPTION_CHARS) ||
    (tiktokCaption !== null && tiktokCaption.length > MAX_TIKTOK_CAPTION_CHARS);

  const finish = (ok: boolean, message: string) => {
    void queryClient.invalidateQueries({ queryKey: ["video", "posts"] });
    if (ok) {
      haptics.success();
      toast.success(message);
      onDone();
    } else {
      haptics.error();
      toast.warning(message);
    }
  };

  const approve = useMutation({
    mutationFn: () =>
      videoApi.approve(post.task_id, {
        ...(xCaption !== null && { x_caption: xCaption }),
        ...(tiktokCaption !== null && { tiktok_caption: tiktokCaption }),
      }),
    onSuccess: (result) =>
      finish(
        result.status === "posted" || result.status === "already_posted",
        result.status === "posted" ? "Video posted." : result.detail,
      ),
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  const reject = useMutation({
    mutationFn: (reason: string) => videoApi.reject(post.task_id, reason),
    onSuccess: () => finish(true, "Draft rejected — feedback goes back to the author."),
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="secondary">{post.occasion}</Badge>
        {post.platforms.map((p) => (
          <Badge key={p} variant="outline">
            {p}
          </Badge>
        ))}
        {post.render_status === "failed" && (
          <Badge variant="destructive">render failed</Badge>
        )}
      </div>

      <CutPlayer post={post} />

      {post.platforms.includes("x") && (
        <CaptionEditor
          platform="X"
          stored={post.x_caption ?? ""}
          maxChars={MAX_X_CAPTION_CHARS}
          value={xCaption}
          onChange={setXCaption}
        />
      )}
      {post.platforms.includes("tiktok") && (
        <CaptionEditor
          platform="TikTok"
          stored={post.tiktok_caption ?? ""}
          maxChars={MAX_TIKTOK_CAPTION_CHARS}
          value={tiktokCaption}
          onChange={setTiktokCaption}
        />
      )}

      <PrimaryAction
        text="Post video"
        disabled={overLimit}
        loading={approve.isPending}
        onClick={() => approve.mutate()}
      />
      <RejectForm
        minChars={MIN_REJECT_CHARS}
        placeholder="What's wrong with this video?"
        pending={reject.isPending}
        onSubmit={(reason) => reject.mutate(reason)}
      />
    </div>
  );
}
