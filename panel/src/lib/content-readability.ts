/**
 * Shared thresholds for auto-collapsing long content in the task detail
 * view (CollapsibleSection, progress updates, checkpoints, acceptance
 * criteria) so a task with a long history doesn't force continuous
 * scrolling through fully-expanded sections.
 *
 * Usage:
 * - CollapsibleSection passes `content` prop; component auto-collapses if content exceeds thresholds
 * - TabProgress (ProgressUpdatesSection, CheckpointsSection) calls exceedsReadabilityThreshold() directly
 *   to decide if an entry defaults open (only 2 most recent open by default; others checked against thresholds)
 * - AcceptanceCriteria passes criteria text to CollapsibleSection
 *
 * Examples:
 * - "Step 1: Do X\nStep 2: Do Y" (2 lines, 26 chars) → starts expanded
 * - "Detailed description of implementation...\n...[11+ lines or >640 chars]" → starts collapsed
 */
export const READABILITY_LINE_THRESHOLD = 10;
export const READABILITY_CHAR_THRESHOLD = 640;

/**
 * True when content exceeds readability thresholds and should default to
 * collapsed to keep the page scrollable on long tasks.
 *
 * A task with 30+ progress entries or 20+ acceptance criteria can fill the
 * entire viewport when all sections are expanded. This function prevents
 * that by collapsing sections with long individual entries, keeping the
 * most-recent few visible while older/verbose entries require a click to expand.
 */
export function exceedsReadabilityThreshold(content: string): boolean {
  if (!content) return false;
  const lineCount = content.split("\n").length;
  return (
    lineCount > READABILITY_LINE_THRESHOLD ||
    content.length > READABILITY_CHAR_THRESHOLD
  );
}
