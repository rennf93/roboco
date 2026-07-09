// Default sample props for local preview (npx hyperframes preview). The
// video-renderer sidecar OVERWRITES this file at render time with whatever
// input_props were stamped via propose_video. Shared by both vertical.html
// and square.html - orientation is baked into which HTML file is rendered
// (see motion/README.md), so there is no runtime branch here.
//
// The three release cards (title/version/chip) are baked directly into the
// HTML, not templated here - this is a one-off recap of three releases that
// already shipped (0.18.0, 0.19.0, 0.20.0), not a reusable per-release
// template like release-announcement. Only the intro line and the closing
// toast are exposed as props.
window.__PROPS__ = {
  introText: "3 releases in 6 days",
  toastTitle: "3 releases shipped",
  toastBody: "0.18.0 to 0.20.0 in six days.",
};
window.__ORIENTATION__ = "vertical";
