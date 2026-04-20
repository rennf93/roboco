import type { NextConfig } from "next";

// const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",

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
