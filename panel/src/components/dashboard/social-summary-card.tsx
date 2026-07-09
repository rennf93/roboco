"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { xApi, videoApi } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Share2 } from "lucide-react";

// Compact stand-in for the full X/video queues on the command center — the
// full queues (plus the unified history) moved to /social to avoid a
// duplicated surface. Reuses the same queries the queues themselves run
// (["x","posts"] / ["video","posts"]), so react-query dedups the fetch once
// /social's own queues are also mounted.
export function SocialSummaryCard({ className }: { className?: string }) {
  const { data: xPosts } = useQuery({
    queryKey: ["x", "posts"],
    queryFn: () => xApi.listPosts(),
    refetchInterval: 30000,
  });
  const { data: videoPosts } = useQuery({
    queryKey: ["video", "posts"],
    queryFn: () => videoApi.listPosts(),
    refetchInterval: 30000,
  });

  const xCount = xPosts?.length ?? 0;
  const videoCount = videoPosts?.length ?? 0;
  const total = xCount + videoCount;

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Share2 className="h-5 w-5" />
          Social
          {total > 0 && <Badge variant="secondary">{total}</Badge>}
        </CardTitle>
        <CardDescription>
          Held X and video drafts awaiting your approval.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-4 text-sm text-muted-foreground">
          <span>
            {xCount} X draft{xCount === 1 ? "" : "s"}
          </span>
          <span>
            {videoCount} video draft{videoCount === 1 ? "" : "s"}
          </span>
        </div>
        <Link href="/social" prefetch={false}>
          <Button variant="outline" size="sm">
            Open Social
          </Button>
        </Link>
      </CardContent>
    </Card>
  );
}
