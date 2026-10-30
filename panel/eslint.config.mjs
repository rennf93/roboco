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
  // @next/next/no-location-assign-relative-destination points component code
  // at redirect() or useRouter().push(). The API client's 401 handler is an
  // axios interceptor: it runs outside React, where neither exists, and its
  // full-page load of /login is what drops the signed-out session's client
  // state (React Query cache, stores). Scoped to that one file so a relative
  // location.href/assign anywhere else still reports.
  {
    files: ["src/lib/api/client.ts"],
    rules: { "@next/next/no-location-assign-relative-destination": "off" },
  },
  // The test setup declares jest-dom's matchers on Vitest's `Matchers` by
  // declaration merging, which takes an interface whose only content is the
  // `extends` clause. `with-single-extends` is the rule's own option for that
  // case; it still reports any other empty interface or `{}` type there.
  {
    files: ["src/test/setup.ts"],
    rules: {
      "@typescript-eslint/no-empty-object-type": [
        "error",
        { allowInterfaces: "with-single-extends" },
      ],
    },
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
