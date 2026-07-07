// Default sample props for local preview (npx hyperframes preview). The
// video-renderer sidecar OVERWRITES this file at render time with the real
// per-release values + window.__ORIENTATION__.
window.__PROPS__ = {
  script: "Ship day. Faster renders, sharper docs, one less thing to babysit.",
  version: "0.19.0",
  highlights: [
    "Fable-mode adopted fleet-wide",
    "FE/UX-UI design bar shipped",
    "Feature spotlight goes live on X",
  ],
};
window.__ORIENTATION__ = "vertical";