import type { NextConfig } from "next";
import path from "path";

// const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",

  // Pin the file-tracing root to this directory so `output: "standalone"` is
  // deterministic regardless of stray lockfiles higher up the tree (e.g. a
  // ~/package-lock.json). Without it Next can infer the wrong workspace root
  // from a sibling lockfile and nest the standalone output, so `server.js`
  // never lands at `.next/standalone/server.js` and `node server.js` fails.
  outputFileTracingRoot: path.join(__dirname),

  // // Proxy API calls to the backend to avoid CORS issues
  // async rewrites() {
  //   return [
  //     {
  //       source: "/api/:path*",
  //       destination: `${BACKEND_URL}/api/:path*`,
  //     },
  //     {
  //       source: "/ws/:path*",
  //       destination: `${BACKEND_URL}/ws/:path*`,
  //     },
  //   ];
  // },
};

export default nextConfig;
