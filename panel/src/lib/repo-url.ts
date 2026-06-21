/**
 * Helpers to turn a project's stored `git_url` into clickable GitHub-style
 * web URLs for branches and PRs.
 *
 * `git_url` may be an https clone URL (`https://github.com/owner/repo.git`),
 * an ssh URL (`git@github.com:owner/repo.git`), or already a web URL — with or
 * without a trailing `.git`. Each helper returns `null` when it can't build a
 * usable URL, so callers render a plain (non-link) label as a graceful
 * fallback rather than a broken link.
 */

/** Normalize a git_url to its web base, e.g. `https://github.com/owner/repo`. */
export function repoWebUrl(gitUrl: string | null | undefined): string | null {
  if (!gitUrl) return null;
  let url = gitUrl.trim();
  // ssh form: git@host:owner/repo(.git) -> https://host/owner/repo
  const ssh = url.match(/^git@([^:]+):(.+)$/);
  if (ssh) {
    url = `https://${ssh[1]}/${ssh[2]}`;
  }
  url = url.replace(/\.git$/, "").replace(/\/+$/, "");
  return /^https?:\/\//.test(url) ? url : null;
}

/** Web URL for a branch, e.g. `…/repo/tree/feature/backend/abc`. */
export function branchUrl(
  gitUrl: string | null | undefined,
  branch: string | null | undefined,
): string | null {
  const base = repoWebUrl(gitUrl);
  if (!base || !branch) return null;
  // roboco branch names are url-safe ([a-z0-9/_-]); slashes are kept so GitHub
  // resolves the full ref path.
  return `${base}/tree/${branch}`;
}

/** Web URL for a pull request, e.g. `…/repo/pull/54`. */
export function pullUrl(
  gitUrl: string | null | undefined,
  prNumber: number | null | undefined,
): string | null {
  const base = repoWebUrl(gitUrl);
  if (!base || !prNumber) return null;
  return `${base}/pull/${prNumber}`;
}
