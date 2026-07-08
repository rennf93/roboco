import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { type ReactNode } from "react";
import { PageRefreshProvider } from "@/components/page-refresh-provider";
import { usePageRefresh } from "@/hooks/use-page-refresh";

function wrapper({ children }: { children: ReactNode }) {
  return <PageRefreshProvider>{children}</PageRefreshProvider>;
}

describe("usePageRefresh", () => {
  it("returns the page refresh context value", () => {
    const { result } = renderHook(() => usePageRefresh(), { wrapper });

    expect(result.current.activeScope).toBeNull();
    expect(typeof result.current.refresh).toBe("function");
    expect(typeof result.current.register).toBe("function");
    expect(typeof result.current.unregister).toBe("function");
    expect(typeof result.current.setActiveScope).toBe("function");
  });

  it("throws when used outside of PageRefreshProvider", () => {
    // Suppress the expected error message in test output.
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => renderHook(() => usePageRefresh())).toThrow(
      "usePageRefresh must be used within a PageRefreshProvider",
    );

    consoleSpy.mockRestore();
  });
});
