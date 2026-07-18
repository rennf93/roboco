"use client";

import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";

/**
 * Shared project (repository) badge — renders nothing when neither field is
 * present. Used by both x-post-queue.tsx and video-post-queue.tsx, whose
 * queue rows are otherwise identical here except for the tooltip wording.
 *
 * @example
 * ```tsx
 * <ProjectBadge
 *   slug={post.project_slug}
 *   name={post.project_name}
 *   label="The project (repository) this draft targets"
 * />
 * ```
 */
export function ProjectBadge({
  slug,
  name,
  label,
}: {
  slug?: string | null;
  name?: string | null;
  /** Tooltip text — phrase it per caller ("draft" vs "video"). */
  label: string;
}) {
  if (!slug && !name) return null;
  return (
    <HelpTip label={label}>
      <Badge variant="outline">{name || slug}</Badge>
    </HelpTip>
  );
}
