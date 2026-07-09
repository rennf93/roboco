// Default sample props for local preview (npx hyperframes preview). The
// video-renderer sidecar OVERWRITES this file at render time with the real
// per-request values + window.__ORIENTATION__. Shared by both vertical.html
// and square.html — orientation is baked into which HTML file is rendered
// (see motion/README.md), so there is no runtime branch here.
window.__PROPS__ = {
  taskTitle: "Ship the panel-demo kit",
  agentName: "fe-dev-1",
  teamLabel: "Frontend",
  priorityLabel: "P1 - High",
  toastTitle: "Task completed",
  toastBody: "fe-dev-1 merged the panel-demo kit PR.",
};
window.__ORIENTATION__ = "vertical";
