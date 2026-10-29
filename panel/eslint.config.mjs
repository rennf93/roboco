import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // react-hooks/incompatible-library reports that React Compiler would skip
  // memoizing a component that calls an API it can't memoize safely. The
  // panel doesn't run React Compiler (next.config.ts sets no reactCompiler),
  // and the kanban column needs TanStack Virtual's useVirtualizer, whose
  // instance methods read live scroll state: that component is memoized by
  // hand (memo() over a stabilized tasks slice) and must never be
  // compiler-memoized, so the report is the intended state, not a defect.
  // Scoped to that one file so any other incompatible call still reports.
  {
    files: ["src/components/kanban/core/kanban-column.tsx"],
    rules: { "react-hooks/incompatible-library": "off" },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Generated coverage output — not source files
    "coverage/**",
  ]),
]);

export default eslintConfig;
