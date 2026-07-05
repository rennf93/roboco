// Local authoring config (Remotion Studio / `remotion render` from a dev's
// workspace). The remotion-renderer sidecar does NOT read this file — it
// drives `@remotion/bundler` + `@remotion/renderer` programmatically and
// passes its own render options directly (see remotion-renderer/render.js).
import {Config} from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setChromiumOpenGlRenderer("angle");
