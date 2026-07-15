/**
 * Validate a URL search-param tab/view value against a known set, falling
 * back to a default when it is null, empty, or not in the set. Replaces the
 * bare `as T || default` cast that only guards null and lets a typo value
 * blank the active tab + content pane.
 */
export function pickTab<T extends string>(
  raw: string | null,
  valid: readonly T[],
  fallback: T,
): T {
  return valid.includes(raw as T) ? (raw as T) : fallback;
}