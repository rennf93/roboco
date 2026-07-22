"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  a2aApi,
  type AdminReplyRequest,
  type CreateCeoConversationRequest,
} from "@/lib/api/a2a";

export const a2aLiveKeys = {
  all: ["a2a-live"] as const,
  conversations: ["a2a-live", "conversations"] as const,
  ceoConversations: ["a2a-live", "ceo-conversations"] as const,
  pairs: ["a2a-live", "pairs"] as const,
  messages: (conversationId: string) =>
    ["a2a-live", "messages", conversationId] as const,
};

// Conversation list — refreshed by WS `a2a.message` invalidation and the
// manual Refresh button; a short staleTime keeps remounts reasonably fresh.
// `refetchInterval` is an unconditional poll the caller drives at a faster
// cadence while /ws/system is down (the desktop view: 20s connected / 8s
// disconnected) — it never gates off entirely, only speeds up.
export function useA2AConversations(
  limit?: number,
  enabled = true,
  refetchInterval: number | false = false,
) {
  return useQuery({
    queryKey: [...a2aLiveKeys.conversations, limit ?? 50],
    queryFn: () => a2aApi.listAdminConversations(limit),
    staleTime: 30_000,
    enabled,
    refetchInterval,
  });
}

// The CEO's own threads (participant-scoped) — resolved peer + per-thread
// unread count. The phone chat's "Mine" list.
export function useCeoConversations(limit?: number, enabled = true) {
  return useQuery({
    queryKey: [...a2aLiveKeys.ceoConversations, limit ?? 50],
    queryFn: () => a2aApi.listCeoConversations(limit),
    staleTime: 30_000,
    enabled,
  });
}

// Clear a thread's unread counter when it's opened. Invalidates the CEO
// list so its badge drops without waiting for the next WS frame.
export function useMarkConversationRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) =>
      a2aApi.markConversationRead(conversationId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: a2aLiveKeys.ceoConversations,
      });
    },
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
// `refetchInterval` defaults to off; the desktop A2A page passes the same
// unconditional poll conversations get (20s connected / 8s disconnected —
// never gated off), while the /tg Mini App chat tab gates its own ~10s poll
// off entirely whenever its WS is connected or demo mode is active.
export function useA2AMessages(
  conversationId: string | null,
  options?: { refetchInterval?: number | false; enabled?: boolean },
) {
  return useQuery({
    queryKey: a2aLiveKeys.messages(conversationId || ""),
    queryFn: () => a2aApi.listAdminMessages(conversationId!),
    enabled: !!conversationId && (options?.enabled ?? true),
    staleTime: 30_000,
    refetchInterval: options?.refetchInterval ?? false,
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

// CEO opens (or reopens) a fresh 1:1 with an agent, sending the first
// message in the same call. Invalidates the conversation list so the new
// thread appears; the caller selects it into view once the id comes back.
export function useCreateCeoConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateCeoConversationRequest) =>
      a2aApi.createConversation(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: a2aLiveKeys.conversations });
      queryClient.invalidateQueries({
        queryKey: a2aLiveKeys.ceoConversations,
      });
    },
  });
}

export interface SendCeoMessageVariables {
  conversationId: string;
  content: string;
}

// CEO sends a follow-up message in a conversation it already owns (the plain
// send route, not the watched-conversation interject-as-ceo path).
export function useSendCeoMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ conversationId, content }: SendCeoMessageVariables) =>
      a2aApi.sendCeoMessage(conversationId, content),
    onSuccess: (_sent, variables) => {
      queryClient.invalidateQueries({ queryKey: a2aLiveKeys.conversations });
      queryClient.invalidateQueries({
        queryKey: a2aLiveKeys.ceoConversations,
      });
      queryClient.invalidateQueries({
        queryKey: a2aLiveKeys.messages(variables.conversationId),
      });
    },
  });
}
