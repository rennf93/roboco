"use client";

/**
 * One normalized queue over the CEO-decision held-draft sources (release
 * proposal, X posts, video posts, roadmap items, and the proposed Pest
 * Control / Spackle / Scales / Dogfood board-program items) so the cockpit
 * renders a single card stack. Query keys and cadence match the desktop
 * queue components — the two surfaces share the cache when both are open.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { releaseApi, type ReleaseProposal } from "@/lib/api/release";
import { xApi, type XPost } from "@/lib/api/x";
import { videoApi, type VideoPost } from "@/lib/api/video";
import { roadmapApi, type RoadmapItem } from "@/lib/api/roadmap";
import { pestControlApi, type PestHuntItem } from "@/lib/api/pest-control";
import { spackleApi, type GapFillItem } from "@/lib/api/spackle";
import { scalesApi, type RebalanceItem } from "@/lib/api/scales";
import { dogfoodApi, type FrictionFixItem } from "@/lib/api/dogfood";
import { isTgDemoMode } from "@/lib/telegram/demo";

const demo = () => import("@/lib/telegram/demo-data");

// Pest Control / Spackle / Dogfood items share one evidence-backed shape;
// Scales rebalances a live task instead, so it carries its own arm below.
type ProgramItem = PestHuntItem | GapFillItem | FrictionFixItem;

export type ApprovalItem =
  | { kind: "release"; id: string; proposal: ReleaseProposal }
  | { kind: "x_post"; id: string; post: XPost }
  | { kind: "video_post"; id: string; post: VideoPost }
  | { kind: "roadmap"; id: string; cycleId: string; item: RoadmapItem }
  | {
      kind: "pest_control" | "spackle" | "dogfood";
      id: string;
      cycleId: string;
      item: ProgramItem;
    }
  | { kind: "scales"; id: string; cycleId: string; item: RebalanceItem };

const REFETCH_MS = 30_000;

export function useApprovalQueue() {
  const release = useQuery({
    queryKey: ["release", "proposal"],
    queryFn: async () =>
      isTgDemoMode() ? (await demo()).DEMO_RELEASE : releaseApi.getProposal(),
    refetchInterval: REFETCH_MS,
  });
  const xPosts = useQuery({
    queryKey: ["x", "posts"],
    queryFn: async () =>
      isTgDemoMode() ? (await demo()).DEMO_X_POSTS : xApi.listPosts(),
    refetchInterval: REFETCH_MS,
  });
  const videoPosts = useQuery({
    queryKey: ["video", "posts"],
    queryFn: async () =>
      isTgDemoMode() ? (await demo()).DEMO_VIDEO_POSTS : videoApi.listPosts(),
    refetchInterval: REFETCH_MS,
  });
  const roadmap = useQuery({
    queryKey: ["roadmap", "cycles"],
    queryFn: async () =>
      isTgDemoMode() ? (await demo()).DEMO_ROADMAP : roadmapApi.listCycles(),
    refetchInterval: REFETCH_MS,
  });
  const pestControl = useQuery({
    queryKey: ["pest-control", "cycles"],
    queryFn: async () =>
      isTgDemoMode()
        ? (await demo()).DEMO_PEST_CONTROL
        : pestControlApi.listCycles(),
    refetchInterval: REFETCH_MS,
  });
  const spackle = useQuery({
    queryKey: ["spackle", "cycles"],
    queryFn: async () =>
      isTgDemoMode() ? (await demo()).DEMO_SPACKLE : spackleApi.listCycles(),
    refetchInterval: REFETCH_MS,
  });
  const scales = useQuery({
    queryKey: ["scales", "cycles"],
    queryFn: async () =>
      isTgDemoMode() ? (await demo()).DEMO_SCALES : scalesApi.listCycles(),
    refetchInterval: REFETCH_MS,
  });
  const dogfood = useQuery({
    queryKey: ["dogfood", "cycles"],
    queryFn: async () =>
      isTgDemoMode() ? (await demo()).DEMO_DOGFOOD : dogfoodApi.listCycles(),
    refetchInterval: REFETCH_MS,
  });

  const items = useMemo<ApprovalItem[]>(() => {
    const out: ApprovalItem[] = [];
    if (release.data) {
      out.push({
        kind: "release",
        id: release.data.task_id,
        proposal: release.data,
      });
    }
    for (const post of xPosts.data ?? []) {
      out.push({ kind: "x_post", id: post.task_id, post });
    }
    for (const post of videoPosts.data ?? []) {
      out.push({ kind: "video_post", id: post.task_id, post });
    }
    for (const cycle of roadmap.data ?? []) {
      for (const item of cycle.items) {
        if (item.status === "proposed") {
          out.push({
            kind: "roadmap",
            // Item ids are only unique within their cycle.
            id: `${cycle.task_id}:${item.id}`,
            cycleId: cycle.task_id,
            item,
          });
        }
      }
    }
    // The four board-program cycles share the roadmap shape: only proposed
    // items await a decision, ids are per-cycle.
    for (const cycle of pestControl.data ?? []) {
      for (const item of cycle.items) {
        if (item.status === "proposed") {
          out.push({
            kind: "pest_control",
            id: `${cycle.task_id}:${item.id}`,
            cycleId: cycle.task_id,
            item,
          });
        }
      }
    }
    for (const cycle of spackle.data ?? []) {
      for (const item of cycle.items) {
        if (item.status === "proposed") {
          out.push({
            kind: "spackle",
            id: `${cycle.task_id}:${item.id}`,
            cycleId: cycle.task_id,
            item,
          });
        }
      }
    }
    for (const cycle of scales.data ?? []) {
      for (const item of cycle.items) {
        if (item.status === "proposed") {
          out.push({
            kind: "scales",
            id: `${cycle.task_id}:${item.id}`,
            cycleId: cycle.task_id,
            item,
          });
        }
      }
    }
    for (const cycle of dogfood.data ?? []) {
      for (const item of cycle.items) {
        if (item.status === "proposed") {
          out.push({
            kind: "dogfood",
            id: `${cycle.task_id}:${item.id}`,
            cycleId: cycle.task_id,
            item,
          });
        }
      }
    }
    return out;
  }, [
    release.data,
    xPosts.data,
    videoPosts.data,
    roadmap.data,
    pestControl.data,
    spackle.data,
    scales.data,
    dogfood.data,
  ]);

  const queries = [
    release,
    xPosts,
    videoPosts,
    roadmap,
    pestControl,
    spackle,
    scales,
    dogfood,
  ];
  return {
    items,
    isLoading: queries.some((q) => q.isLoading),
    // A failed source contributes no items, which would silently read as
    // "queue is clear" — surface it so an outage never masquerades as an
    // empty queue.
    anyFailed: queries.some((q) => q.isError),
  };
}
