import "@testing-library/jest-dom";

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
