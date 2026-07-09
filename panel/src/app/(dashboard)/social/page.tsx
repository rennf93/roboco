"use client";

import { XPostQueue } from "@/components/dashboard/x-post-queue";
import { VideoPipelineStrip } from "@/components/dashboard/video-pipeline-strip";
import { VideoPostQueue } from "@/components/dashboard/video-post-queue";
import { SocialHistorySection } from "@/components/dashboard/social-history-section";

export default function SocialPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Social</h1>
        <p className="text-muted-foreground">
          X and video drafts awaiting your approval, plus everything already
          posted or rejected.
        </p>
      </div>

      <XPostQueue />
      <VideoPipelineStrip />
      <VideoPostQueue />
      <SocialHistorySection />
    </div>
  );
}
