import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// Stub useGitFile so CodeSnippet is tested in isolation from TanStack Query.
const { useGitFile } = vi.hoisted(() => ({ useGitFile: vi.fn() }));

vi.mock("@/hooks/use-git", () => ({ useGitFile }));

import { CodeSnippet } from "../code-snippet";

interface MockReturn {
  data?: {
    branch: string;
    path: string;
    content: string;
    start_line: number;
    total_lines: number;
    truncated: boolean;
  } | null;
  isLoading?: boolean;
  isError?: boolean;
}

function mockReturn(r: MockReturn) {
  useGitFile.mockReturnValue({
    data: r.data ?? null,
    isLoading: r.isLoading ?? false,
    isError: r.isError ?? false,
  });
}

describe("CodeSnippet", () => {
  it("renders line numbers and the truncated note for a loaded slice", () => {
    mockReturn({
      data: {
        branch: "feature/backend/abc",
        path: "roboco/services/task.py",
        content: "import os\n\ndef x():\n    pass",
        start_line: 40,
        total_lines: 200,
        truncated: true,
      },
    });
    const { container } = render(
      <CodeSnippet branch="feature/backend/abc" file="roboco/services/task.py" activeLine={42} />,
    );
    // Line numbers start at 40 for the 4 content lines.
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("43")).toBeInTheDocument();
    expect(screen.getByText("def x():")).toBeInTheDocument();
    expect(
      screen.getByText(/showing lines 40–43 of 200/),
    ).toBeInTheDocument();
    // Sanity: only one snippet block rendered.
    expect(container.querySelectorAll("pre")).toHaveLength(1);
  });

  it("highlights the active line", () => {
    mockReturn({
      data: {
        branch: "b",
        path: "p.py",
        content: "a\nb\nc",
        start_line: 1,
        total_lines: 3,
        truncated: false,
      },
    });
    render(<CodeSnippet branch="b" file="p.py" activeLine={2} />);
    // The active line's content is "b"; its row carries the highlight class.
    const row = screen.getByText("b").parentElement;
    expect(row?.className).toContain("bg-blue-500/15");
    // The active line's number is now a real HelpTip trigger (Radix injects
    // data-state via asChild), not a bare span.
    expect(screen.getByText("2").getAttribute("data-state")).toBe("closed");
  });

  it("renders the fail-open hint on an error", () => {
    mockReturn({ isError: true });
    render(<CodeSnippet branch="b" file="p.py" activeLine={1} />);
    expect(screen.getByText(/Couldn’t load this file/)).toBeInTheDocument();
  });

  it("renders a skeleton while loading", () => {
    mockReturn({ isLoading: true });
    const { container } = render(
      <CodeSnippet branch="b" file="p.py" activeLine={1} />,
    );
    expect(container.querySelector(".animate-pulse")).not.toBeNull();
  });

  it("renders nothing when branch is missing", () => {
    mockReturn({ data: null });
    const { container } = render(
      <CodeSnippet branch={null} file="p.py" activeLine={1} />,
    );
    expect(container.firstChild).toBeNull();
  });
});