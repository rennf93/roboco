// untar -> bundle (cached per source sha256) -> getCompositions ->
// renderMedia. Bundling is the expensive webpack step; the orchestrator
// calls POST /render twice per video (once per orientation) with the
// *same* tarball, so caching the bundle by content hash means the second
// call skips straight to getCompositions/renderMedia instead of
// re-bundling identical source (a documented Remotion anti-pattern).
//
// Only the bundle (the webpack-compiled serveUrl + its extracted source
// dir) is cached, across requests, until evicted. The per-request render
// OUTPUT (the rendered mp4) is never cached — every render call produces
// its own temp file that the caller cleans up once it has streamed the
// response.
import { bundle } from "@remotion/bundler";
import { getCompositions, renderMedia } from "@remotion/renderer";
import { createHash } from "node:crypto";
import { mkdtemp, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import * as tar from "tar";

const MAX_CACHED_BUNDLES = 8;

/** Root for both the extracted-source AND webpack-bundle-output temp dirs,
 * so both halves of one cache entry are easy to spot as a pair on disk. */
const BUNDLE_TMP_ROOT = tmpdir();

/** sha256(tar bytes) -> Promise<{serveUrl, sourceDir, bundleDir}>,
 * oldest-first (Map preserves insertion order; re-inserting a key moves it
 * to the end). */
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
      const { sourceDir, bundleDir } = await promise;
      await Promise.all([
        rm(sourceDir, { recursive: true, force: true }),
        rm(bundleDir, { recursive: true, force: true }),
      ]);
    } catch {
      // Best-effort: a failed bundle was never cached with real dirs, and
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

/**
 * @param {Buffer} tarBuffer
 * @param {string} sha sha256(tarBuffer), already computed by the caller —
 *   threaded through so the bundle-output dir can be named after the same
 *   content hash as the source dir, making an evicted pair easy to
 *   correlate on disk.
 */
async function buildBundle(tarBuffer, sha) {
  const workDir = await mkdtemp(path.join(BUNDLE_TMP_ROOT, "remotion-src-"));
  // bundle({entryPoint}) with no `outDir` silently creates its OWN temp dir
  // (a fresh `remotion-webpack-bundle-*` under the OS tmpdir, ~19MB of
  // webpack output) that Remotion never cleans up and we'd otherwise never
  // learn the path of. Naming it ourselves means we can track + reclaim it
  // exactly like sourceDir below.
  const bundleDir = path.join(BUNDLE_TMP_ROOT, `remotion-webpack-bundle-${sha}`);
  try {
    await extractTar(tarBuffer, workDir);
    const motionDir = path.join(workDir, "motion");
    await linkSharedNodeModules(motionDir);
    const entryPoint = path.join(motionDir, "src", "index.ts");
    const serveUrl = await bundle({ entryPoint, outDir: bundleDir });
    return { serveUrl, sourceDir: workDir, bundleDir };
  } catch (err) {
    await Promise.all([
      rm(workDir, { recursive: true, force: true }).catch(() => {}),
      rm(bundleDir, { recursive: true, force: true }).catch(() => {}),
    ]);
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

  const bundlePromise = buildBundle(tarBuffer, sha);
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

/** Thrown when `composition_id` isn't one of what Root.tsx actually
 * registers — server.js maps this to a 400 instead of the generic 500 a
 * deep-in-renderMedia failure would otherwise produce. */
export class UnknownCompositionError extends Error {
  constructor(compositionId, knownIds) {
    super(
      `Unknown composition_id "${compositionId}". Registered: ${
        knownIds.length ? knownIds.join(", ") : "(none)"
      }`,
    );
    this.name = "UnknownCompositionError";
    this.statusCode = 400;
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
  // goes to both getCompositions and renderMedia; calculateMetadata on
  // the composition itself branches on inputProps.orientation to pick the
  // frame (1080x1920 vs 1080x1080 for ReleaseAnnouncement).
  const mergedProps = { ...inputProps, orientation };

  // getCompositions() evaluates the *actual* Root.tsx this request's tar
  // bundled (never a stale/hardcoded id list) — its returned entries are
  // renderMedia-ready composition objects, so finding by id here also
  // replaces the old selectComposition() call rather than duplicating a
  // second Root evaluation.
  const compositions = await getCompositions(serveUrl, {
    inputProps: mergedProps,
  });
  const composition = compositions.find((c) => c.id === compositionId);
  if (!composition) {
    throw new UnknownCompositionError(
      compositionId,
      compositions.map((c) => c.id),
    );
  }

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
