import { expect } from "vitest";
import * as jestDomMatchers from "@testing-library/jest-dom/matchers";
import type { TestingLibraryMatchers } from "@testing-library/jest-dom/matchers";

// jest-dom's DOM matchers (toBeInTheDocument, toHaveAttribute, ...). Vitest 5
// no longer reads matcher types from the global `jest.Matchers`, and the
// `@testing-library/jest-dom/vitest` entry (7.0.1) still augments Vitest 4's
// one-parameter `Assertion<T>`, which Vitest 5's `Assertion<R, T>` won't merge
// with, so every matcher was untyped (testing-library/jest-dom#738). Register
// the matchers here and declare them on `Matchers<R, T>`, the interface Vitest
// 5 documents for custom matchers (its type parameters must repeat Vitest's
// for the merge to take; the argument order is the one jest-dom#742 uses).
// Once a jest-dom release types Vitest 5, import
// "@testing-library/jest-dom/vitest" instead and drop the declaration.
expect.extend(jestDomMatchers);

declare module "vitest" {
  interface Matchers<R, T> extends TestingLibraryMatchers<T, R> {}
}

// jsdom has no matchMedia implementation. useIsMobile (and anything built on
// it, e.g. ResponsiveTable) calls window.matchMedia unconditionally in an
// effect, so every test needs at least a default (non-matching / desktop)
// stub — individual tests can still override window.matchMedia themselves
// for mobile-branch assertions.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}
