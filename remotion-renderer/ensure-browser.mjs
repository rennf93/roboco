// Build-time only: pre-download Chrome Headless Shell into the image so a
// container's first real render isn't also the first time it fetches a
// browser. Run once as a Docker RUN step (see docker/remotion.Dockerfile).
import { ensureBrowser } from "@remotion/renderer";

await ensureBrowser();
console.log("Chrome Headless Shell ready.");
