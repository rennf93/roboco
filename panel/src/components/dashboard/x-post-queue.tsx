"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { xApi } from "@/lib/api";
import type { XPost, XPostExecuteResult } from "@/lib/api/x";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { HelpTip } from "@/components/ui/help-tip";
import { ProjectBadge } from "@/components/dashboard/project-badge";
import { AtSign, CheckCircle2, Rocket, Sparkles, XCircle } from "lucide-react";
import { toast } from "sonner";

const MAX_TWEET_CHARS = 280;
const _MIN_REASON_CHARS = 4;

function sourceMeta(source: XPost["source"]) {
  if (source === "x_post")
    return {
      label: "Release post",
      icon: Rocket,
      hint: "Drafted automatically when a release publishes",
    };
  if (source === "x_feature")
    return {
      label: "Feature spotlight",
      icon: Sparkles,
      hint: "Drafted periodically by the Head of Marketing's feature-spotlight sweep",
    };
  return {
    label: "Mention reply",
    icon: AtSign,
    hint: "Drafted automatically in reply to a meaningful mention on X",
  };
}

// Explains what Approve does, or why it's disabled — surfaced on the button
// itself so the CEO doesn't have to guess between "already posting", "over
// the limit", or "empty". Always returns a non-empty string (never null):
// HelpTip renders a bare child vs. a Tooltip-wrapped one depending on
// truthiness, and toggling that branch on a live-changing condition (like
// `approving`) unmounts/remounts the child, losing any DOM reference a
// caller captured before the state flip.
function approveHint(
  approving: boolean,
  overLimit: boolean,
  bodyEmpty: boolean,
): string {
  if (approving) return "Already posting this draft";
  if (overLimit)
    return `Over X's ${MAX_TWEET_CHARS}-character limit — trim the draft to enable`;
  if (bodyEmpty) return "Draft body is empty";
  return "Post this draft to X";
}

function describeExecuteResult(result: XPostExecuteResult): string {
  if (result.status === "posted") return "Posted to X.";
  if (result.status === "already_posted") return "Already posted — no-op.";
  if (result.status === "already_in_progress")
    return "A post is already in progress for this draft.";
  if (result.status === "no_credentials")
    return "No X credentials configured — set them below first.";
  return `${result.status}: ${result.detail}`;
}

// One row of the queue: an editable draft body + char counter + approve/reject.
function XPostRow({
  post,
  onApprove,
  onReject,
  approving,
}: {
  post: XPost;
  onApprove: (taskId: string, body: string) => void;
  onReject: (post: XPost) => void;
  approving: boolean;
}) {
  // `edited` holds the user's in-progress textarea input; null means "show
  // the server value". Deriving the displayed body avoids syncing query
  // state into local state with an effect (mirrors TranscriptRetentionCard).
  const [edited, setEdited] = useState<string | null>(null);
  const body = edited ?? post.body;
  const meta = sourceMeta(post.source);
  const overLimit = body.length > MAX_TWEET_CHARS;

  return (
    <div className="rounded-lg border p-4 transition-colors hover:bg-muted/50">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <HelpTip label={meta.hint}>
          <span className="inline-flex items-center gap-1.5">
            <meta.icon className="h-4 w-4 text-muted-foreground" />
            <span className="font-medium">{meta.label}</span>
          </span>
        </HelpTip>
        <ProjectBadge
          slug={post.project_slug}
          name={post.project_name}
          label="The project (repository) this draft targets"
        />
        {post.release_version && (
          <HelpTip label="The release this post announces">
            <Badge variant="outline">v{post.release_version}</Badge>
          </HelpTip>
        )}
        {post.mention && (
          <HelpTip label="The X mention this draft replies to">
            <Badge variant="secondary" className="max-w-56 truncate">
              re: {post.mention.text}
            </Badge>
          </HelpTip>
        )}
        {post.feature && (
          <HelpTip label="The shipped feature this spotlight covers">
            <Badge variant="outline" className="max-w-56 truncate">
              feature: {post.feature.title}
            </Badge>
          </HelpTip>
        )}
      </div>

      <Textarea
        value={body}
        onChange={(e) => setEdited(e.target.value)}
        rows={3}
        className={overLimit ? "border-destructive" : undefined}
      />
      <HelpTip label={`X's per-post character limit (${MAX_TWEET_CHARS})`}>
        <p
          className={`mt-1 text-right text-xs ${
            overLimit ? "text-destructive" : "text-muted-foreground"
          }`}
        >
          {body.length}/{MAX_TWEET_CHARS}
        </p>
      </HelpTip>

      <div className="mt-2 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
        <HelpTip label="Cancels this draft — it will not be posted">
          <Button
            variant="outline"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => onReject(post)}
          >
            <XCircle className="mr-1 h-4 w-4" />
            Reject
          </Button>
        </HelpTip>
        <HelpTip
          label={approveHint(approving, overLimit, body.trim().length === 0)}
        >
          <Button
            size="sm"
            className="bg-green-600 hover:bg-green-700"
            disabled={approving || overLimit || body.trim().length === 0}
            onClick={() => onApprove(post.task_id, body)}
          >
            <CheckCircle2 className="mr-1 h-4 w-4" />
            Approve &amp; post
          </Button>
        </HelpTip>
      </div>
    </div>
  );
}

// CEO queue for held X drafts (release posts + mention replies). Hidden when
// empty (mirrors the release-proposal + playbook-review queues). Posted/
// rejected drafts move to the unified history on /social (SocialHistorySection).
export function XPostQueue({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState<XPost | null>(null);
  const [reason, setReason] = useState("");
  const [approvingId, setApprovingId] = useState<string | null>(null);

  const { data: posts, isLoading } = useQuery({
    queryKey: ["x", "posts"],
    queryFn: () => xApi.listPosts(),
    refetchInterval: 30000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["x", "posts"] });

  const approveMutation = useMutation({
    mutationFn: ({ taskId, body }: { taskId: string; body: string }) =>
      xApi.approve(taskId, body),
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
      xApi.reject(taskId, reason),
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

  const handleApprove = (taskId: string, body: string) => {
    setApprovingId(taskId);
    approveMutation.mutate({ taskId, body });
  };

  if (isLoading) return null;

  if (!posts || posts.length === 0) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Rocket className="h-5 w-5" />X Post Queue
          </CardTitle>
          <CardDescription>
            Drafted release announcements, feature spotlights, and mention
            replies (if enabled) land here for you to edit, approve, or reject.
            Nothing posts on its own.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No drafts yet. A post is drafted here when a release publishes — set
            your keys in Settings → X (Twitter) Credentials and enable the X
            engine to start.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Rocket className="h-5 w-5" />X Post Queue
            <HelpTip label="Drafts waiting for your edit, approve, or reject">
              <Badge variant="secondary">{posts.length}</Badge>
            </HelpTip>
          </CardTitle>
          <CardDescription>
            Drafted release announcements, feature spotlights, and mention
            replies — edit, approve (posts to X), or reject. Nothing posts on
            its own.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {posts.map((post) => (
            <XPostRow
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
            <HelpTip label="Stored on the draft and shown in History next to this rejection">
              <Label htmlFor="x-reject-reason">Reason</Label>
            </HelpTip>
            <Textarea
              id="x-reject-reason"
              placeholder="e.g. tone doesn't match our voice; not worth a public reply..."
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
    </>
  );
}
