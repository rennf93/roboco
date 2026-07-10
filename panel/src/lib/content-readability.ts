/**
 * Shared thresholds for auto-collapsing long content in the task detail
 * view (CollapsibleSection, progress updates, checkpoints, acceptance
 * criteria) so a task with a long history doesn't force continuous
 * scrolling through fully-expanded sections.
 */
export const READABILITY_LINE_THRESHOLD = 10;
export const READABILITY_CHAR_THRESHOLD = 640;

/** True when content is long enough that it should default to collapsed. */
export function exceedsReadabilityThreshold(content: string): boolean {
  if (!content) return false;
  const lineCount = content.split("\n").length;
  return (
    lineCount > READABILITY_LINE_THRESHOLD ||
    content.length > READABILITY_CHAR_THRESHOLD
  );
}
