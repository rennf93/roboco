import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CommandPalette } from "@/components/layout/command-palette";
import { useCommandPalette } from "@/hooks/use-command-palette";

vi.mock("@/hooks/use-command-palette", () => ({
  useCommandPalette: vi.fn(),
}));

const mockedUseCommandPalette = vi.mocked(useCommandPalette);

function baseHookState(
  overrides: Partial<ReturnType<typeof useCommandPalette>> = {},
) {
  return {
    open: true,
    setOpen: vi.fn(),
    query: "",
    setQuery: vi.fn(),
    groups: [],
    flatItems: [],
    selectedIndex: 0,
    moveSelection: vi.fn(),
    selectCurrent: vi.fn(),
    navigateTo: vi.fn(),
    ...overrides,
  };
}

describe("CommandPalette", () => {
  beforeEach(() => {
    mockedUseCommandPalette.mockReset();
  });

  it("shows the empty-recents message when there are no recents and the query is empty", () => {
    mockedUseCommandPalette.mockReturnValue(baseHookState());
    render(<CommandPalette />);
    expect(screen.getByText("No recent items yet")).toBeInTheDocument();
  });

  it("shows the no-results message when a query returns nothing", () => {
    mockedUseCommandPalette.mockReturnValue(baseHookState({ query: "zzz" }));
    render(<CommandPalette />);
    expect(screen.getByText("No results")).toBeInTheDocument();
  });

  it("renders grouped results with subtitles from live data", () => {
    mockedUseCommandPalette.mockReturnValue(
      baseHookState({
        query: "dev",
        groups: [
          {
            label: "Agents",
            items: [
              {
                type: "agent",
                id: "fe-dev-2",
                title: "FE-Dev-2",
                subtitle: "@fe-dev-2",
                href: "/agents/fe-dev-2",
              },
            ],
          },
        ],
        flatItems: [
          {
            type: "agent",
            id: "fe-dev-2",
            title: "FE-Dev-2",
            subtitle: "@fe-dev-2",
            href: "/agents/fe-dev-2",
          },
        ],
      }),
    );
    render(<CommandPalette />);
    expect(screen.getByText("Agents")).toBeInTheDocument();
    expect(screen.getByText("FE-Dev-2")).toBeInTheDocument();
    expect(screen.getByText("@fe-dev-2")).toBeInTheDocument();
  });

  it("wires arrow keys and Enter on the input to moveSelection/selectCurrent", () => {
    const moveSelection = vi.fn();
    const selectCurrent = vi.fn();
    mockedUseCommandPalette.mockReturnValue(
      baseHookState({ moveSelection, selectCurrent }),
    );
    render(<CommandPalette />);
    const input = screen.getByRole("combobox");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowUp" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(moveSelection).toHaveBeenCalledWith(1);
    expect(moveSelection).toHaveBeenCalledWith(-1);
    expect(selectCurrent).toHaveBeenCalledTimes(1);
  });

  it("navigates when a result is clicked", () => {
    const navigateTo = vi.fn();
    const item = {
      type: "task" as const,
      id: "abc123ef",
      title: "Fix the thing",
      subtitle: "#abc123ef",
      href: "/tasks/abc123ef",
    };
    mockedUseCommandPalette.mockReturnValue(
      baseHookState({
        query: "fix",
        groups: [{ label: "Tasks", items: [item] }],
        flatItems: [item],
        navigateTo,
      }),
    );
    render(<CommandPalette />);
    fireEvent.click(screen.getByText("Fix the thing"));
    expect(navigateTo).toHaveBeenCalledWith(item);
  });

  it("opens the palette on Cmd+K / Ctrl+K", () => {
    const setOpen = vi.fn();
    mockedUseCommandPalette.mockReturnValue(
      baseHookState({ open: false, setOpen }),
    );
    render(<CommandPalette />);

    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(setOpen).toHaveBeenCalledWith(true);

    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(setOpen).toHaveBeenCalledWith(true);
  });

  it("closes on Escape via Radix Dialog's built-in dismiss", () => {
    const setOpen = vi.fn();
    mockedUseCommandPalette.mockReturnValue(baseHookState({ setOpen }));
    render(<CommandPalette />);

    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Escape" });

    expect(setOpen).toHaveBeenCalledWith(false);
  });
});
