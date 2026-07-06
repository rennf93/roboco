// video-renderer — HTTP sidecar for the RoboCo video engine.
//
// POST /render accepts a gzipped tar of a motion/ composition source (arcname
// "motion") plus {composition_id, orientation, input_props} form fields, and
// responds with the rendered MP4 as the raw response body. GET /health is a
// plain liveness probe. Credential-free and git-free: this process never
// holds a git token, never shells out to git, and only ever reads what the
// orchestrator POSTs to it.
import express from "express";
import multer from "multer";
import rateLimit from "express-rate-limit";
import { createReadStream } from "node:fs";
import { renderComposition, UnknownCompositionError } from "./render.js";

const PORT = Number(process.env.PORT ?? 3001);

// motion/ source (no node_modules, no build output) is a few hundred KB in
// practice; this cap is generous headroom, not a tuned budget.
const MAX_UPLOAD_BYTES = 256 * 1024 * 1024;

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: MAX_UPLOAD_BYTES },
});

const app = express();

// This sidecar is container-network-only (no published ports) with a single
// trusted caller (the orchestrator), which already renders cuts serially.
// The limiter is not the primary control — it's a cheap ceiling against a
// runaway retry storm or a misbehaving caller tying up Chrome headless +
// the render temp dir. 30/min is well above any legitimate render rate
// (each render takes seconds, ~a few calls/min) so it never blocks real use.
const renderLimiter = rateLimit({
  windowMs: 60_000,
  max: 30,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "rate limit: too many render requests" },
});

app.get("/health", (_req, res) => {
  res.status(200).json({ status: "ok" });
});

app.post("/render", renderLimiter, upload.single("source"), async (req, res) => {
  const body = req.body ?? {};
  const compositionId = body.composition_id;
  const orientation = body.orientation;
  const inputPropsRaw = body.input_props;

  if (!req.file) {
    res
      .status(400)
      .json({ error: "missing 'source' file field (gzipped tar of motion/)" });
    return;
  }
  if (!compositionId || typeof compositionId !== "string") {
    res.status(400).json({ error: "missing 'composition_id' field" });
    return;
  }
  if (orientation !== "vertical" && orientation !== "square") {
    res
      .status(400)
      .json({ error: "'orientation' must be 'vertical' or 'square'" });
    return;
  }

  let inputProps;
  try {
    inputProps = inputPropsRaw ? JSON.parse(inputPropsRaw) : {};
  } catch {
    res.status(400).json({ error: "'input_props' is not valid JSON" });
    return;
  }

  try {
    const { outputLocation, cleanup } = await renderComposition({
      tarBuffer: req.file.buffer,
      compositionId,
      inputProps,
      orientation,
    });

    res.status(200);
    res.setHeader("Content-Type", "video/mp4");
    const stream = createReadStream(outputLocation);
    stream.on("error", (err) => {
      console.error("video-renderer: stream error", err);
      if (!res.headersSent) {
        res.status(500);
      }
      res.end();
      cleanup();
    });
    stream.on("close", () => {
      cleanup();
    });
    // stream.pipe() never propagates a DESTINATION close back to the
    // source: if the client aborts, or the orchestrator's retry-on-timeout
    // hangs up mid-download, `res` closes but the source stream's own
    // "close" above never fires — leaking this request's render-output
    // temp dir on every such disconnect. Destroying the still-open source
    // releases its fd immediately; cleanup() is idempotent (rm force:true)
    // so also landing here on a normal end-of-stream close is harmless.
    res.on("close", () => {
      stream.destroy();
      cleanup();
    });
    stream.pipe(res);
  } catch (err) {
    if (err instanceof UnknownCompositionError) {
      res.status(err.statusCode).json({ error: err.message });
      return;
    }
    const message = err instanceof Error ? err.message : String(err);
    console.error("video-renderer: render failed", message);
    res.status(500).json({ error: `render failed: ${message}` });
  }
});

// 4-arg signature required for Express to treat this as error-handling
// middleware. Catches errors `next()`-ed from earlier middleware — in
// practice, today, that's multer rejecting an oversized/malformed upload
// before our route body ever runs (otherwise Express's default handler
// returns a generic 500 HTML page instead of a clear JSON 4xx).
app.use((err, req, res, _next) => {
  if (err instanceof multer.MulterError) {
    const status = err.code === "LIMIT_FILE_SIZE" ? 413 : 400;
    res.status(status).json({ error: `upload rejected: ${err.message}` });
    return;
  }
  const message = err instanceof Error ? err.message : String(err);
  console.error("video-renderer: unhandled error", message);
  res.status(500).json({ error: "internal error" });
});

app.listen(PORT, () => {
  console.log(`video-renderer listening on :${PORT}`);
});
