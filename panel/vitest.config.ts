import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    coverage: {
      provider: "v8",
      include: [
        "src/app/**",
        "src/components/**",
        "src/hooks/**",
        "src/lib/**",
        "src/store/**",
        "src/proxy.ts",
      ],
      reporter: ["text", "lcov", "json-summary"],
      // One floor per source tree: each tree's coverage when these were set,
      // rounded down, so a change that lowers a tree's coverage fails
      // `pnpm test`. Raise a number when its tree's coverage rises. Not
      // `perFile`: that holds every file to the number on its own, and many
      // files have no test yet, so any number above 0 would fail.
      thresholds: {
        "src/app/**": {
          lines: 39,
          statements: 39,
          functions: 37,
          branches: 34,
        },
        "src/components/**": {
          lines: 53,
          statements: 52,
          functions: 49,
          branches: 46,
        },
        "src/hooks/**": {
          lines: 42,
          statements: 42,
          functions: 32,
          branches: 36,
        },
        "src/lib/**": {
          lines: 23,
          statements: 22,
          functions: 17,
          branches: 22,
        },
        "src/store/**": {
          lines: 81,
          statements: 79,
          functions: 71,
          branches: 90,
        },
        "src/proxy.ts": {
          lines: 100,
          statements: 96,
          functions: 66,
          branches: 100,
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
