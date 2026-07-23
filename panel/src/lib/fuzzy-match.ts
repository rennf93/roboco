/**
 * Case-insensitive fuzzy match: a query matches a target when every query
 * character appears in the target in order (not necessarily contiguous).
 * Lower scores rank first. An exact substring hit ranks by how early it
 * starts; a non-contiguous subsequence hit is penalized by the total gap
 * between matched characters so tighter matches still outrank loose ones.
 * Returns null when the query isn't a subsequence of the target at all.
 */
export function fuzzyScore(query: string, target: string): number | null {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const t = target.toLowerCase();

  const substringIndex = t.indexOf(q);
  if (substringIndex !== -1) return substringIndex;

  let searchFrom = 0;
  let gap = 0;
  let lastMatch = -1;
  for (const ch of q) {
    const found = t.indexOf(ch, searchFrom);
    if (found === -1) return null;
    if (lastMatch !== -1) gap += found - lastMatch - 1;
    lastMatch = found;
    searchFrom = found + 1;
  }
  return t.length + gap;
}

/** True when `query` fuzzy-matches `target` (see `fuzzyScore`). */
export function fuzzyMatch(query: string, target: string): boolean {
  return fuzzyScore(query, target) !== null;
}
