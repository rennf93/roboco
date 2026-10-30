import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { ConventionsStandard } from "@/lib/api/conventions";

// ---------------------------------------------------------------------------
// Mock @tanstack/react-query so we can control useQuery/useMutation return
// values without a real QueryClientProvider.
// ---------------------------------------------------------------------------

const { mockUseQuery, mockUseMutation, mockMutate } = vi.hoisted(() => ({
  mockUseQuery: vi.fn(),
  mockUseMutation: vi.fn(),
  mockMutate: vi.fn(),
}));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQuery: mockUseQuery,
    useMutation: mockUseMutation,
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  };
});

vi.mock("@/lib/api/conventions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/conventions")>();
  return {
    ...actual,
    conventionsApi: {
      get: vi.fn(),
      update: vi.fn(),
      restore: vi.fn(),
      findings: vi.fn().mockResolvedValue([]),
    },
  };
});

// ---------------------------------------------------------------------------
// Import component AFTER mocks are set up
// ---------------------------------------------------------------------------

import { ConventionsTab } from "../conventions-tab";

// ---------------------------------------------------------------------------
// Helpers. The mock MUST mirror the backend ConventionsStandard schema field
// for field (repo lesson: invented-shape mocks stay green under a broken
// save), including the infra declaration.
// ---------------------------------------------------------------------------

function buildStandard(overrides: Partial<ConventionsStandard> = {}): ConventionsStandard {
  return {
    version: 1,
    languages: ["python", "typescript"],
    modules: [
      { path: "roboco/services", purpose: "business logic", forbidden: ["route"] },
    ],
    rules: { no_lint_suppressions: { name: "no_lint_suppressions", level: "block" } },
    custom: [],
    waivers: [],
    infra: ["Dockerfile*", "docker/**"],
    ...overrides,
  };
}

function setup(standard: ConventionsStandard) {
  mockUseQuery.mockImplementation((args: { queryKey: string[] }) => {
    if (args.queryKey[0] === "conventions") {
      return {
        data: { standard, health: { status: "ok", head_sha: "s", last_ok_sha: null } },
        isLoading: false,
      };
    }
    return { data: [], isLoading: false };
  });
  mockUseMutation.mockReturnValue({ mutate: mockMutate, isPending: false });
}

function lastMutatedStandard(): ConventionsStandard {
  const call = mockMutate.mock.calls.at(-1);
  expect(call).toBeDefined();
  return call![0] as ConventionsStandard;
}

describe("ConventionsTab — infrastructure paths card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders declared infra paths and preserves them through an edit + save", () => {
    setup(buildStandard());
    render(<ConventionsTab projectId="p1" />);

    expect(screen.getByText("Infrastructure paths")).toBeInTheDocument();
    const globInput = screen.getAllByPlaceholderText(
      "infra/glob",
    )[0] as HTMLInputElement;
    expect(globInput.value).toBe("Dockerfile*");

    // Edit the first declared glob and save: the round-trip must carry the
    // edited infra list AND every other section (the strip-on-save failure
    // mode this card exists to prevent).
    fireEvent.change(globInput, { target: { value: "Dockerfile.custom*" } });
    fireEvent.click(screen.getByText("Save to repo"));

    const saved = lastMutatedStandard();
    expect(saved.infra).toEqual(["Dockerfile.custom*", "docker/**"]);
    expect(saved.modules).toHaveLength(1);
    expect(saved.rules.no_lint_suppressions).toBeDefined();
    expect(saved.version).toBe(1);
  });

  it("preserves an explicit empty infra list (opt-out) through save", () => {
    setup(buildStandard({ infra: [] }));
    render(<ConventionsTab projectId="p1" />);

    expect(
      screen.getByText("Opted out: no path requires the DevOps reviewer."),
    ).toBeInTheDocument();

    // Save is disabled until there is a draft: add a row (an edit) then
    // remove it again, leaving the draft's infra list empty, and save.
    fireEvent.click(screen.getByText("Add path"));
    fireEvent.click(screen.getAllByText("Remove").at(-1)!);
    fireEvent.click(screen.getByText("Save to repo"));

    // The empty list is a real declaration: saving it must not coerce it
    // back to defaults or drop the key.
    expect(lastMutatedStandard().infra).toEqual([]);
  });

  it("shows the shipped-defaults state for an undeclared infra section", () => {
    setup(buildStandard({ infra: null }));
    render(<ConventionsTab projectId="p1" />);

    expect(
      screen.getByText(/Not declared: the shipped defaults apply/),
    ).toBeInTheDocument();

    // Adding the first path declares the section (overriding defaults).
    fireEvent.click(screen.getByText("Add path"));
    fireEvent.click(screen.getByText("Save to repo"));

    expect(lastMutatedStandard().infra).toEqual([""]);
  });
});
