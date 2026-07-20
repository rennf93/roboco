import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import React from "react";

const {
  getCredentialsStatus,
  listInstallations,
  listInstallationRepositories,
} = vi.hoisted(() => ({
  getCredentialsStatus: vi.fn(async () => ({ has_credentials: true })),
  listInstallations: vi.fn(async () => [{ id: 1, account_login: "acme" }]),
  listInstallationRepositories: vi.fn(async () => [
    {
      full_name: "acme/widgets",
      clone_url: "https://github.com/acme/widgets.git",
      private: true,
    },
    {
      full_name: "acme/gizmos",
      clone_url: "https://github.com/acme/gizmos.git",
      private: false,
    },
  ]),
}));

vi.mock("@/lib/api", () => ({
  githubAppApi: {
    getCredentialsStatus,
    listInstallations,
    listInstallationRepositories,
  },
}));

// Functional Select mock (mirrors a2a-reply-composer.test.tsx): SelectItem
// renders as a clickable button wired to onValueChange via context, so a
// real "pick an installation" interaction can be simulated without Radix's
// portal/pointer machinery.
vi.mock("@/components/ui/select", () => {
  const Ctx = React.createContext<(v: string) => void>(() => {});
  return {
    Select: ({
      onValueChange,
      children,
    }: {
      onValueChange?: (v: string) => void;
      children: React.ReactNode;
    }) => (
      <Ctx.Provider value={onValueChange ?? (() => {})}>
        {children}
      </Ctx.Provider>
    ),
    SelectTrigger: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectValue: () => null,
    SelectContent: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectItem: ({
      value,
      children,
    }: {
      value: string;
      children: React.ReactNode;
    }) => {
      const onValueChange = React.useContext(Ctx);
      return (
        <button type="button" onClick={() => onValueChange(value)}>
          {children}
        </button>
      );
    },
  };
});

import { SelectRepoDialog } from "../select-repo-picker";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

// Waits for the credentials-status query to resolve (button starts disabled
// until `configured` is known) before clicking — otherwise the click lands on
// a still-disabled button and silently no-ops.
async function openPicker(onSelect = vi.fn()) {
  render(withQueryClient(<SelectRepoDialog onSelect={onSelect} />));
  const button = await screen.findByRole("button", { name: /Select repo/i });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  return onSelect;
}

describe("SelectRepoDialog", () => {
  beforeEach(() => {
    getCredentialsStatus.mockClear();
    listInstallations.mockClear();
    listInstallationRepositories.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("disables the button when the GitHub App isn't configured", async () => {
    getCredentialsStatus.mockResolvedValueOnce({ has_credentials: false });
    render(withQueryClient(<SelectRepoDialog onSelect={vi.fn()} />));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Select repo/i }),
      ).toBeDisabled(),
    );
    expect(listInstallations).not.toHaveBeenCalled();
  });

  it("auto-applies the sole installation and lists its repositories", async () => {
    await openPicker();
    expect(await screen.findByText("Select a repository")).toBeInTheDocument();
    expect(await screen.findByText("acme/widgets")).toBeInTheDocument();
    expect(screen.getByText("acme/gizmos")).toBeInTheDocument();
    await waitFor(() =>
      expect(listInstallationRepositories).toHaveBeenCalledWith(1),
    );
  });

  it("shows an installation picker when there is more than one, and only loads repos after picking", async () => {
    listInstallations.mockResolvedValueOnce([
      { id: 1, account_login: "acme" },
      { id: 2, account_login: "widgets-inc" },
    ]);
    await openPicker();
    await screen.findByText("Select a repository");

    expect(screen.getByText("acme")).toBeInTheDocument();
    expect(screen.getByText("widgets-inc")).toBeInTheDocument();
    expect(listInstallationRepositories).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("widgets-inc"));
    await waitFor(() =>
      expect(listInstallationRepositories).toHaveBeenCalledWith(2),
    );
  });

  it("filters the repository list by search text", async () => {
    await openPicker();
    await screen.findByText("acme/widgets");

    fireEvent.change(screen.getByPlaceholderText("Search repositories..."), {
      target: { value: "gizmo" },
    });

    expect(screen.queryByText("acme/widgets")).not.toBeInTheDocument();
    expect(screen.getByText("acme/gizmos")).toBeInTheDocument();
  });

  it("picking a repo hands back its clone_url + installation id and closes the dialog", async () => {
    const onSelect = await openPicker();
    await screen.findByText("acme/widgets");

    fireEvent.click(screen.getByText("acme/widgets"));

    expect(onSelect).toHaveBeenCalledWith({
      git_url: "https://github.com/acme/widgets.git",
      installation_id: 1,
    });
    await waitFor(() =>
      expect(screen.queryByText("Select a repository")).not.toBeInTheDocument(),
    );
  });
});
