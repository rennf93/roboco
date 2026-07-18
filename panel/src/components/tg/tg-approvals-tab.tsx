"use client";

import { ReleaseProposalCard } from "@/components/dashboard/release-proposal-card";
import { XPostQueue } from "@/components/dashboard/x-post-queue";
import { VideoPostQueue } from "@/components/dashboard/video-post-queue";
import { RoadmapReviewQueue } from "@/components/dashboard/roadmap-review-queue";

/**
 * The CEO's held-artifact stack, vertically stacked for a single thumb
 * scroll column. Every card is the exact same self-contained
 * dashboard-layout-independent component the desktop dashboard renders —
 * each already fetches its own data and no-ops (renders nothing useful) when
 * empty, so there's nothing to compose here beyond stacking them.
 */
export function TgApprovalsTab() {
  return (
    <div className="space-y-4">
      <ReleaseProposalCard />
      <XPostQueue />
      <VideoPostQueue />
      <RoadmapReviewQueue />
    </div>
  );
}
