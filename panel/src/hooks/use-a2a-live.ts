"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { a2aApi, type AdminReplyRequest } from "@/lib/api/a2a";

export const a2aLiveKeys = {
  all: ["a2a-live"] as const,
  conversations: ["a2a-live", "conversations"] as const,
  pairs: ["a2a-live", "pairs"] as const,
  messages: (conversationId: string) =>
    ["a2a-live", "messages", conversationId] as const,
};

// Conversation list — refreshed by WS `a2a.message` invalidation and the
// manual Refresh button; a short staleTime keeps remounts reasonably fresh.
export function useA2AConversations(limit?: number) {
  return useQuery({
    queryKey: [...a2aLiveKeys.conversations, limit ?? 50],
    queryFn: () => a2aApi.listAdminConversations(limit),
    staleTime: 30_000,
  });
}

// Switchboard pair cards (the org-chart view) — every allowed agent pair
// joined with its representative conversation stats. Refreshed by WS
// `a2a.message` invalidation, same as the conversation list.
export function useA2AAdminPairs() {
  return useQuery({
    queryKey: a2aLiveKeys.pairs,
    queryFn: () => a2aApi.listAdminPairs(),
    staleTime: 30_000,
  });
}

// Transcript for one conversation. WS frames for the selected conversation
// invalidate this key; full bodies always come from REST (excerpts are capped).
export function useA2AMessages(conversationId: string | null) {
  return useQuery({
    queryKey: a2aLiveKeys.messages(conversationId || ""),
    queryFn: () => a2aApi.listAdminMessages(conversationId!),
    enabled: !!conversationId,
    staleTime: 30_000,
  });
}

export interface ReplyAsCeoVariables extends AdminReplyRequest {
  conversationId: string;
}

// CEO chime-in. The reply is a direct CEO->to_agent message (pairwise model);
// invalidate the list (the CEO<->agent conversation appears/updates there) and
// the watched transcript's messages.
export function useReplyAsCeo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ conversationId, ...reply }: ReplyAsCeoVariables) =>
      a2aApi.replyAsCeo(conversationId, reply),
    onSuccess: (_sent, variables) => {
      queryClient.invalidateQueries({ queryKey: a2aLiveKeys.conversations });
      queryClient.invalidateQueries({
        queryKey: a2aLiveKeys.messages(variables.conversationId),
      });
    },
  });
}
