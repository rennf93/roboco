import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { AgentRole, Team } from "@/types";

// Covers the `excludeRoles` prop added for the A2A "New DM" agent picker:
// the CEO (role=ceo, team=board) would otherwise land in the Board group
// like any other board member, letting the CEO pick itself as a DM target.

const { useAgentDefinitions } = vi.hoisted(() => ({
  useAgentDefinitions: vi.fn(),
}));

vi.mock("@/hooks/use-agents", () => ({ useAgentDefinitions }));

// Render Select content directly — no Radix portal/pointer machinery needed
// for a static "which items are present" assertion.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectTrigger: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectValue: ({ placeholder }: { placeholder?: string }) => (
    <span>{placeholder}</span>
  ),
  SelectContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectGroup: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectLabel: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectItem: ({
    children,
    value,
  }: {
    children: React.ReactNode;
    value: string;
  }) => <div data-value={value}>{children}</div>,
}));

import { AgentSelector } from "../agent-selector";

const AGENTS = [
  {
    id: "ceo",
    uuid: "uuid-ceo",
    name: "Renzo",
    role: AgentRole.CEO,
    team: Team.BOARD,
  },
  {
    id: "product-owner",
    uuid: "uuid-po",
    name: "Product Owner",
    role: AgentRole.PRODUCT_OWNER,
    team: null,
  },
  {
    id: "be-dev-1",
    uuid: "uuid-bd1",
    name: "Backend Developer 1",
    role: AgentRole.DEVELOPER,
    team: Team.BACKEND,
  },
];

describe("AgentSelector excludeRoles", () => {
  beforeEach(() => {
    useAgentDefinitions.mockReturnValue({ data: AGENTS, isLoading: false });
  });

  it("includes the CEO in the Board group by default", () => {
    render(<AgentSelector value={null} onChange={vi.fn()} />);
    expect(screen.getByText("Renzo")).toBeInTheDocument();
  });

  it("drops the CEO when excludeRoles=[AgentRole.CEO], keeping everyone else", () => {
    const { container } = render(
      <AgentSelector
        value={null}
        onChange={vi.fn()}
        excludeRoles={[AgentRole.CEO]}
      />,
    );
    expect(screen.queryByText("Renzo")).not.toBeInTheDocument();
    expect(container.querySelector('[data-value="ceo"]')).toBeNull();
    expect(
      container.querySelector('[data-value="product-owner"]'),
    ).not.toBeNull();
    expect(container.querySelector('[data-value="be-dev-1"]')).not.toBeNull();
  });
});
