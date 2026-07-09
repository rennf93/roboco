// untar -> write per-render props.js -> HyperFrames createRenderJob +
// executeRenderJob. HyperFrames has no webpack bundling step (it loads the
// HTML directly in headless Chrome and captures frames via the beginFrame
// API), so the bundle-cache machinery the old path carried is gone
// — every /render call extracts its own temp dir, renders, and cleans up.
//
// The per-request render OUTPUT (the rendered mp4) is never cached — every
// render call produces its own temp file the caller cleans up once it has
// streamed the response.
import { createRenderJob, executeRenderJob } from "@hyperframes/producer";
import { existsSync } from "node:fs";
import { cp, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import * as tar from "tar";

const FPS = 30;
// @hyperframes/producer reads each cut's dimensions from the composition HTML
// itself (data-width/data-height on the stage), so the sidecar no longer
// passes width/height — it only picks the quality tier.
const QUALITY = "high";

// Caps the DECOMPRESSED size (a gzip bomb inflates a tiny upload into a huge
// tar stream); MAX_UPLOAD_BYTES in server.js only bounds the compressed
// bytes on the wire.
const MAX_EXTRACTED_BYTES = Number(
  process.env.MAX_EXTRACTED_BYTES ?? 512 * 1024 * 1024,
);

/** Thrown when the tar stream's cumulative entry size crosses
 * MAX_EXTRACTED_BYTES — server.js maps this to a 413. */
export class ExtractedSizeExceededError extends Error {
  constructor(maxBytes) {
    super(`extracted archive exceeds ${maxBytes} byte cap`);
    this.name = "ExtractedSizeExceededError";
    this.statusCode = 413;
  }
}

async function extractTar(tarBuffer, destDir) {
  await new Promise((resolve, reject) => {
    let extractedBytes = 0;
    // ponytail: header-declared entry.size, summed per entry via onentry —
    // not a byte-exact streaming cap, but tar headers carry the true
    // (post-gunzip) size, so this catches a bomb before most of it lands.
    const extractor = tar.extract({
      cwd: destDir,
      onentry: (entry) => {
        extractedBytes += entry.size;
        if (extractedBytes > MAX_EXTRACTED_BYTES) {
          extractor.destroy(new ExtractedSizeExceededError(MAX_EXTRACTED_BYTES));
        }
      },
    });
    extractor.on("finish", resolve);
    extractor.on("error", reject);
    Readable.from(tarBuffer).pipe(extractor);
  });
}

// Deliberately under the orchestrator's 600s client-side HTTP timeout, so
// this fires first and the caller gets a clean error instead of an abandoned
// connection while Chrome is still wedged server-side.
const RENDER_TIMEOUT_SECONDS = Number(
  process.env.RENDER_TIMEOUT_SECONDS ?? 570,
);

/** Thrown when a render exceeds RENDER_TIMEOUT_SECONDS — server.js maps
 * this to a 500 and then hard-exits the process (see server.js for why). */
export class RenderTimeoutError extends Error {
  constructor(seconds) {
    super(`render exceeded ${seconds}s timeout`);
    this.name = "RenderTimeoutError";
    this.statusCode = 500;
  }
}

/** Thrown when the requested orientation's HTML file isn't present in the
 * composition dir — server.js maps this to a 400 instead of the generic 500
 * a deep-in-executeRenderJob failure would otherwise produce. The known
 * ids are the `<orientation>.html` files actually on disk (vertical.html /
 * square.html), so the error names what the caller can use. */
export class UnknownCompositionError extends Error {
  constructor(compositionId, knownIds) {
    super(
      `Unknown composition_id/orientation "${compositionId}". Available: ${
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
 * the whole file in RAM twice). The callback also removes the temp extract
 * dir — the caller never needs to know it existed.
 */
export async function renderComposition({
  tarBuffer,
  compositionId,
  inputProps,
  orientation,
}) {
  const extractDir = await mkdtemp(path.join(tmpdir(), "hyperframes-src-"));
  let outDir;
  try {
    await extractTar(tarBuffer, extractDir);
    // compositionId is validated to [A-Za-z0-9_-]+ at the HTTP boundary
    // (server.js), so it can't escape compositionsRoot — but enforce it here
    // too so render.js stays safe regardless of caller.
    const compositionsRoot = path.resolve(
      extractDir,
      "motion",
      "compositions",
    );
    const compositionDir = path.resolve(compositionsRoot, compositionId);
    const compositionsRootWithSep = `${compositionsRoot}${path.sep}`;
    if (
      compositionDir !== compositionsRoot &&
      !compositionDir.startsWith(compositionsRootWithSep)
    ) {
      throw new UnknownCompositionError(compositionId, []);
    }
    // ponytail: readdir is the smallest thing that fails if the dir is
    // missing OR the orientation file is missing — one stat-vs-readdir
    // branch collapsed into a single listing used for the 400 error.
    let knownIds;
    try {
      const entries = await readdir(compositionDir);
      knownIds = entries
        .filter((name) => name === "vertical.html" || name === "square.html")
        .sort();
    } catch {
      knownIds = [];
    }
    if (!knownIds.includes(`${orientation}.html`)) {
      throw new UnknownCompositionError(compositionId, knownIds);
    }

    // The HTML files <script src="props.js"></script> reads this — set up by
    // Task T2, but the sidecar must write the file per render so the HTML
    // picks up per-release content + orientation.
    const propsJs =
      `window.__PROPS__ = ${JSON.stringify(inputProps ?? {})}; ` +
      `window.__ORIENTATION__ = ${JSON.stringify(orientation)};`;
    await writeFile(path.join(compositionDir, "props.js"), propsJs);

    // @hyperframes/producer serves the compiled entry at the file-server ROOT
    // (/index.html) and every other asset from projectDir at its relative path.
    // So projectDir must be the composition dir — theme.css/props.js are direct
    // children — and the shared motion/public tree, which theme.css references
    // as ../../public/fonts and the root-served entry clamps to /public/fonts,
    // must be staged into it or the fonts 404 and fall back to system faces.
    const publicSrc = path.join(extractDir, "motion", "public");
    if (existsSync(publicSrc)) {
      await cp(publicSrc, path.join(compositionDir, "public"), {
        recursive: true,
      });
    }

    outDir = await mkdtemp(path.join(tmpdir(), "hyperframes-out-"));
    const outputLocation = path.join(outDir, "render.mp4");

    // createRenderJob carries only render params; @hyperframes/producer@0.7.36
    // takes the source dir + output path as executeRenderJob args (they moved
    // out of the job config), and resolves the cut's HTML from entryFile
    // relative to projectDir.
    const job = createRenderJob({
      fps: FPS,
      quality: QUALITY,
      format: "mp4",
      entryFile: `${orientation}.html`,
    });
    let timer;
    try {
      const timeout = new Promise((_resolve, reject) => {
        timer = setTimeout(
          () => reject(new RenderTimeoutError(RENDER_TIMEOUT_SECONDS)),
          RENDER_TIMEOUT_SECONDS * 1000,
        );
      });
      await Promise.race([
        executeRenderJob(job, compositionDir, outputLocation, (progress) => {
          console.log(
            `hyperframes-renderer: ${compositionId}/${orientation} ${Math.round(
              (progress?.percent ?? 0) * 100,
            )}%`,
          );
        }),
        timeout,
      ]);
    } catch (err) {
      await rm(outDir, { recursive: true, force: true }).catch(() => {});
      throw err;
    } finally {
      // Clear on both success AND timeout-throw so a completed render never
      // leaves a dangling timer that fires the watchdog's exit path late.
      clearTimeout(timer);
    }

    return {
      outputLocation,
      cleanup: () =>
        Promise.all([
          rm(outDir, { recursive: true, force: true }).catch(() => {}),
          rm(extractDir, { recursive: true, force: true }).catch(() => {}),
        ]),
    };
  } catch (err) {
    // Reclaim whatever temp dirs exist. outDir is created at the mkdtemp
    // above, so a sync throw from createRenderJob (post-mkdtemp, not
    // awaited) leaves an empty outDir behind — reclaim it too. Re-throw
    // so server.js maps the failure to 4xx/5xx.
    await rm(extractDir, { recursive: true, force: true }).catch(() => {});
    if (outDir) {
      await rm(outDir, { recursive: true, force: true }).catch(() => {});
    }
    throw err;
  }
}