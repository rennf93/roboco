// untar -> bundle (cached per source sha256) -> selectComposition ->
// renderMedia. Bundling is the expensive webpack step; the orchestrator
// calls POST /render twice per video (once per orientation) with the
// *same* tarball, so caching the bundle by content hash means the second
// call skips straight to selectComposition/renderMedia instead of
// re-bundling identical source (a documented Remotion anti-pattern).
//
// Only the bundle (the webpack-compiled serveUrl + its extracted source
// dir) is cached, across requests, until evicted. The per-request render
// OUTPUT (the rendered mp4) is never cached — every render call produces
// its own temp file that the caller cleans up once it has streamed the
// response.
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import { createHash } from "node:crypto";
import { mkdtemp, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import * as tar from "tar";

const MAX_CACHED_BUNDLES = 8;

/** sha256(tar bytes) -> Promise<{serveUrl, sourceDir}>, oldest-first (Map
 * preserves insertion order; re-inserting a key moves it to the end). */
const bundleCache = new Map();

function markRecentlyUsed(sha, promise) {
  bundleCache.delete(sha);
  bundleCache.set(sha, promise);
}

async function evictExcess() {
  while (bundleCache.size > MAX_CACHED_BUNDLES) {
    const oldestSha = bundleCache.keys().next().value;
    const promise = bundleCache.get(oldestSha);
    bundleCache.delete(oldestSha);
    try {
      const { sourceDir } = await promise;
      await rm(sourceDir, { recursive: true, force: true });
    } catch {
      // Best-effort: a failed bundle was never cached with a real dir, and
      // a stat/rm race on an already-gone temp dir is harmless either way.
    }
  }
}

async function extractTar(tarBuffer, destDir) {
  await new Promise((resolve, reject) => {
    const extractor = tar.extract({ cwd: destDir });
    extractor.on("finish", resolve);
    extractor.on("error", reject);
    Readable.from(tarBuffer).pipe(extractor);
  });
}

/** The extracted motion/ ships no node_modules (never committed/tarred).
 * Symlink this sidecar's own pre-installed remotion/react/etc so webpack's
 * module resolution finds them without a per-render `npm install` —
 * motion/'s package.json is kept version-aligned with this image's
 * dependencies by construction (both pin the same `remotion` major). */
async function linkSharedNodeModules(motionDir) {
  const target = path.join(process.cwd(), "node_modules");
  const linkPath = path.join(motionDir, "node_modules");
  await symlink(target, linkPath, "dir");
}

async function buildBundle(tarBuffer) {
  const workDir = await mkdtemp(path.join(tmpdir(), "remotion-src-"));
  try {
    await extractTar(tarBuffer, workDir);
    const motionDir = path.join(workDir, "motion");
    await linkSharedNodeModules(motionDir);
    const entryPoint = path.join(motionDir, "src", "index.ts");
    const serveUrl = await bundle({ entryPoint });
    return { serveUrl, sourceDir: workDir };
  } catch (err) {
    await rm(workDir, { recursive: true, force: true }).catch(() => {});
    throw err;
  }
}

async function getOrCreateBundle(tarBuffer) {
  const sha = createHash("sha256").update(tarBuffer).digest("hex");

  const existing = bundleCache.get(sha);
  if (existing) {
    markRecentlyUsed(sha, existing);
    const { serveUrl } = await existing;
    return serveUrl;
  }

  const bundlePromise = buildBundle(tarBuffer);
  bundleCache.set(sha, bundlePromise);

  try {
    const { serveUrl } = await bundlePromise;
    await evictExcess();
    return serveUrl;
  } catch (err) {
    bundleCache.delete(sha);
    throw err;
  }
}

/**
 * Render one composition/orientation cut. Returns the temp mp4 path plus a
 * cleanup callback the caller MUST invoke once the response has been sent
 * (streamed bytes, not returned in-memory, so a large render never holds
 * the whole file in RAM twice).
 */
export async function renderComposition({
  tarBuffer,
  compositionId,
  inputProps,
  orientation,
}) {
  const serveUrl = await getOrCreateBundle(tarBuffer);

  // The one Remotion v4 fact that makes two aspect ratios work from one
  // timeline: there is no width/height render parameter. The identical
  // inputProps object (here: the caller's props merged with `orientation`)
  // goes to both selectComposition and renderMedia; calculateMetadata on
  // the composition itself branches on inputProps.orientation to pick the
  // frame (1080x1920 vs 1080x1080 for ReleaseAnnouncement).
  const mergedProps = { ...inputProps, orientation };

  const composition = await selectComposition({
    serveUrl,
    id: compositionId,
    inputProps: mergedProps,
  });

  const outDir = await mkdtemp(path.join(tmpdir(), "remotion-out-"));
  const outputLocation = path.join(outDir, "render.mp4");

  try {
    await renderMedia({
      composition,
      serveUrl,
      codec: "h264",
      outputLocation,
      inputProps: mergedProps,
      chromiumOptions: { enableMultiProcessOnLinux: true },
    });
  } catch (err) {
    await rm(outDir, { recursive: true, force: true }).catch(() => {});
    throw err;
  }

  return {
    outputLocation,
    cleanup: () => rm(outDir, { recursive: true, force: true }).catch(() => {}),
  };
}
