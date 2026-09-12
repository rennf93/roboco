import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Team, type Project } from "@/types";

// The HTTP client (axios instance) is mocked at the module level so the REAL
// typed client functions in lib/api/projects run and each test can assert the
// exact requested path (POST/DELETE /projects/{id}/access/{agent_id}).
const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
}));
vi.mock("@/lib/api/client", () => ({ default: api }));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const ROSTER = [
  {
    id: "be-dev-1",
    uuid: "uuid-bd1",
    name: "Backend Developer 1",
    role: "developer",
    team: "backend",
  },
  {
    id: "be-dev-2",
    uuid: "uuid-bd2",
    name: "Backend Developer 2",
    role: "developer",
    team: "backend",
  },
  {
    id: "be-qa",
    uuid: "uuid-qa",
    name: "Backend QA",
    role: "qa",
    team: "backend",
  },
  {
    id: "fe-dev-1",
    uuid: "uuid-fd1",
    name: "Frontend Developer 1",
    role: "developer",
    team: "frontend",
  },
];

const { useAgentDefinitions } = vi.hoisted(() => ({
  useAgentDefinitions: vi.fn(),
}));
vi.mock("@/hooks/use-agents", () => ({ useAgentDefinitions }));

// The card's picker is the shared AgentSelector (a Radix Select). Stub it
// with one button per roster entry so tests click "pick-<slug>" instead of
// driving Radix's portal/pointer machinery — the card's own contract is just
// onChange(slug).
vi.mock("@/components/agents/agent-selector", () => ({
  AgentSelector: ({ onChange }: { onChange: (v: string | null) => void }) => (
    <div>
      {ROSTER.map((a) => (
        <button key={a.id} type="button" onClick={() => onChange(a.id)}>
          pick-{a.id}
        </button>
      ))}
    </div>
  ),
}));

import { toast } from "sonner";
import { AccessCard } from "../access-card";

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "proj-1",
    name: "RoboCo API",
    slug: "roboco-api",
    git_url: "https://github.com/org/repo.git",
    git_provider: "github",
    github_installation_id: null,
    default_branch: "main",
    environments: null,
    protected_branches: ["main"],
    assigned_cell: Team.BACKEND,
    has_git_token: true,
    is_active: true,
    test_command: null,
    lint_command: null,
    format_command: null,
    typecheck_command: null,
    build_command: null,
    quality_command: null,
    codegen_command: null,
    ci_watch_enabled: false,
    ci_watch_workflow: null,
    video_engine_enabled: false,
    dep_update_command: null,
    dep_update_paths: null,
    monthly_budget_usd: null,
    sandbox_services: null,
    sandbox_extensions: null,
    board_programs: null,
    workspace_path: null,
    last_synced_at: null,
    head_commit: null,
    created_by: "ceo",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

const ALLOWED = [
  { id: "uuid-bd1", slug: "be-dev-1", name: "Backend Developer 1" },
  { id: "uuid-qa", slug: "be-qa", name: "Backend QA" },
];

function renderCard(project: Project) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AccessCard project={project} />
    </QueryClientProvider>,
  );
}

describe("AccessCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Re-arm the default get stub — clearAllMocks wipes implementations.
    api.get.mockResolvedValue({ data: {} });
    useAgentDefinitions.mockReturnValue({
      data: ROSTER,
      isLoading: false,
    });
  });

  it("renders the cell-default state when no restriction is in place", () => {
    renderCard(makeProject());

    expect(
      screen.getByText("All agents in the backend cell (default)"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Restricted to/)).not.toBeInTheDocument();
    // The picker still renders so the CEO can restrict from the default state.
    expect(screen.getByText("pick-be-dev-1")).toBeInTheDocument();
  });

  it("also treats a legacy null allowed_agents as the cell default", () => {
    renderCard(makeProject({ access_restricted: false, allowed_agents: null }));

    expect(
      screen.getByText("All agents in the backend cell (default)"),
    ).toBeInTheDocument();
  });

  it("renders the restricted state with the allowed agents by name", () => {
    renderCard(
      makeProject({ access_restricted: true, allowed_agents: ALLOWED }),
    );

    expect(screen.getByText("Restricted to 2 agents")).toBeInTheDocument();
    expect(screen.getByText("Backend Developer 1")).toBeInTheDocument();
    expect(screen.getByText("be-dev-1")).toBeInTheDocument();
    expect(screen.getByText("Backend QA")).toBeInTheDocument();
  });

  it("warns on a restricted-but-empty list (nobody can claim)", () => {
    renderCard(makeProject({ access_restricted: true, allowed_agents: [] }));

    expect(screen.getByText("Restricted to 0 agents")).toBeInTheDocument();
    expect(
      screen.getByText(/no agent can claim this project/i),
    ).toBeInTheDocument();
  });

  it("adds an agent by requesting POST /projects/{id}/access/{agent_id}", async () => {
    const user = userEvent.setup();
    api.post.mockResolvedValue({
      data: makeProject({
        access_restricted: true,
        allowed_agents: [{ id: "uuid-bd2", slug: "be-dev-2", name: "Backend Developer 2" }],
      }),
    });
    renderCard(makeProject());

    await user.click(screen.getByText("pick-be-dev-2"));
    await user.click(screen.getByRole("button", { name: /^Add$/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/projects/proj-1/access/uuid-bd2",
      ),
    );
    expect(toast.success).toHaveBeenCalledWith(
      "Backend Developer 2 can now access this project",
    );
  });

  it("removes an agent by requesting DELETE /projects/{id}/access/{agent_id}", async () => {
    const user = userEvent.setup();
    api.delete.mockResolvedValue({
      data: makeProject({
        access_restricted: true,
        allowed_agents: [ALLOWED[1]],
      }),
    });
    renderCard(
      makeProject({ access_restricted: true, allowed_agents: ALLOWED }),
    );

    await user.click(
      screen.getByRole("button", { name: "Remove Backend Developer 1" }),
    );

    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith(
        "/projects/proj-1/access/uuid-bd1",
      ),
    );
    expect(toast.success).toHaveBeenCalledWith(
      "Backend Developer 1 removed from the allowed list",
    );
  });

  it("toast-errors a failed add without crashing", async () => {
    const user = userEvent.setup();
    api.post.mockRejectedValueOnce(new Error("PM only"));
    renderCard(makeProject());

    await user.click(screen.getByText("pick-be-dev-1"));
    await user.click(screen.getByRole("button", { name: /^Add$/i }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Failed to add access: PM only"),
    );
  });

  it("toast-errors a failed remove without crashing", async () => {
    const user = userEvent.setup();
    api.delete.mockRejectedValueOnce(new Error("403"));
    renderCard(
      makeProject({ access_restricted: true, allowed_agents: ALLOWED }),
    );

    await user.click(
      screen.getByRole("button", { name: "Remove Backend Developer 1" }),
    );

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Failed to remove access: 403",
      ),
    );
  });

  it("declines to re-add an agent already on the list", async () => {
    const user = userEvent.setup();
    renderCard(
      makeProject({ access_restricted: true, allowed_agents: ALLOWED }),
    );

    await user.click(screen.getByText("pick-be-dev-1"));
    await user.click(screen.getByRole("button", { name: /^Add$/i }));

    expect(toast.info).toHaveBeenCalledWith(
      "Backend Developer 1 is already on the allowed list",
    );
    expect(api.post).not.toHaveBeenCalled();
  });

  it("reports when every cell agent is already on the list", () => {
    renderCard(
      makeProject({
        access_restricted: true,
        allowed_agents: [
          ...ALLOWED,
          { id: "uuid-bd2", slug: "be-dev-2", name: "Backend Developer 2" },
        ],
      }),
    );

    expect(
      screen.getByText("Every backend agent is already on the allowed list."),
    ).toBeInTheDocument();
    expect(screen.queryByText("pick-be-dev-1")).toBeNull();
  });
});
