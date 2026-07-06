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
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import * as tar from "tar";

const FPS = 30;
const DIMENSIONS = {
  vertical: { width: 1080, height: 1920 },
  square: { width: 1080, height: 1080 },
};

async function extractTar(tarBuffer, destDir) {
  await new Promise((resolve, reject) => {
    const extractor = tar.extract({ cwd: destDir });
    extractor.on("finish", resolve);
    extractor.on("error", reject);
    Readable.from(tarBuffer).pipe(extractor);
  });
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
    const compositionDir = path.join(
      extractDir,
      "motion",
      "compositions",
      compositionId,
    );
    const htmlPath = path.join(compositionDir, `${orientation}.html`);
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

    const { width, height } = DIMENSIONS[orientation];
    outDir = await mkdtemp(path.join(tmpdir(), "hyperframes-out-"));
    const outputLocation = path.join(outDir, "render.mp4");

    const job = createRenderJob({
      inputPath: htmlPath,
      outputPath: outputLocation,
      width,
      height,
      fps: FPS,
    });
    try {
      await executeRenderJob(job, (progress) => {
        console.log(
          `hyperframes-renderer: ${compositionId}/${orientation} ${Math.round(
            progress.percent * 100,
          )}%`,
        );
      });
    } catch (err) {
      await rm(outDir, { recursive: true, force: true }).catch(() => {});
      throw err;
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
    // On any pre-render failure the out dir was never created — only the
    // extract dir needs reclaiming. Re-throw so server.js maps it to 4xx/5xx.
    await rm(extractDir, { recursive: true, force: true }).catch(() => {});
    throw err;
  }
}