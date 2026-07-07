// Build-time only: verify Chrome Headless Shell is installed so a
// container's first real render isn't also the first time it fetches a
// browser. Run once as a Docker RUN step after `pnpm install` (see
// docker/video-renderer.Dockerfile).
//
// The actual download is performed by puppeteer's postinstall (approved in
// pnpm-workspace.yaml), which lands chrome-headless-shell in
// ~/.cache/puppeteer/chrome-headless-shell/ — the cache
// @hyperframes/engine's resolveHeadlessShellPath scans at render time. This
// script just verifies the binary is present and fails loud if not, so a
// broken/missed download surfaces at image build time, not mid-render.
import { resolveHeadlessShellPath } from "@hyperframes/engine";

const shellPath = await resolveHeadlessShellPath();
if (!shellPath) {
  console.error(
    "Chrome Headless Shell not found in ~/.cache/puppeteer/chrome-headless-shell/. " +
      "puppeteer's postinstall (approved in pnpm-workspace.yaml) should have downloaded it — " +
      "re-run `pnpm install` or run `npx @puppeteer/browsers install chrome-headless-shell@stable`.",
  );
  process.exit(1);
}
console.log(`Chrome Headless Shell ready at ${shellPath}.`);