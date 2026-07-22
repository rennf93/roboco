"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { xApi, type XPost } from "@/lib/api/x";
import { getErrorMessage } from "@/lib/api/client";
import { haptics } from "@/lib/telegram/webapp";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { PrimaryAction } from "./primary-action";
import { RejectForm } from "./reject-form";
import { cn } from "@/lib/utils";

const MAX_TWEET_CHARS = 280;
const MIN_REJECT_CHARS = 4;

const SOURCE_LABELS: Record<XPost["source"], string> = {
  x_post: "Release post",
  x_reply: "Reply",
  x_feature: "Feature spotlight",
};

/** Focused X draft: editable body with the live 280 counter, the mention
 * being replied to when there is one, then post or reject. */
export function XPostDetail({
  post,
  onDone,
}: {
  post: XPost;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [edited, setEdited] = useState<string | null>(null);
  const body = edited ?? post.body;
  const overLimit = body.length > MAX_TWEET_CHARS;

  const finish = (ok: boolean, message: string) => {
    void queryClient.invalidateQueries({ queryKey: ["x", "posts"] });
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
      xApi.approve(post.task_id, edited !== null ? body : undefined),
    onSuccess: (result) =>
      finish(
        result.status === "posted" || result.status === "already_posted",
        result.status === "posted" ? "Posted to X." : result.detail,
      ),
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  const reject = useMutation({
    mutationFn: (reason: string) => xApi.reject(post.task_id, reason),
    onSuccess: () => finish(true, "Draft rejected."),
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="secondary">{SOURCE_LABELS[post.source]}</Badge>
        {post.release_version && <Badge>v{post.release_version}</Badge>}
        {post.project_name && (
          <Badge variant="outline">{post.project_name}</Badge>
        )}
      </div>

      {post.mention && (
        <blockquote className="break-words border-l-2 pl-2 text-xs text-muted-foreground">
          {post.mention.text}
        </blockquote>
      )}

      <div className="space-y-1">
        <Textarea
          value={body}
          onChange={(e) => setEdited(e.target.value)}
          rows={5}
          className={cn("text-sm", overLimit && "border-rose-400/60")}
        />
        <p
          className={cn(
            "text-right text-[11px] tabular-nums",
            overLimit ? "text-rose-400" : "text-muted-foreground",
          )}
        >
          {body.length} / {MAX_TWEET_CHARS}
        </p>
      </div>

      <PrimaryAction
        text="Post to X"
        disabled={overLimit || body.trim().length === 0}
        loading={approve.isPending}
        onClick={() => approve.mutate()}
      />
      <RejectForm
        minChars={MIN_REJECT_CHARS}
        placeholder="Why is this draft wrong?"
        pending={reject.isPending}
        onSubmit={(reason) => reject.mutate(reason)}
      />
    </div>
  );
}
