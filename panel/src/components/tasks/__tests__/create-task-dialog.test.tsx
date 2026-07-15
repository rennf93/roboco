import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { mutateAsync } = vi.hoisted(() => ({
  mutateAsync: vi.fn().mockResolvedValue(undefined),
}));

// useCreateTask: just needs a mutateAsync the test can assert on.
vi.mock("@/hooks/use-tasks", () => ({
  useCreateTask: () => ({ mutateAsync, isPending: false }),
}));

// useProducts: one product so the Product <Select> has a pickable option.
vi.mock("@/hooks/use-products", () => ({
  useProducts: () => ({
    data: [{ id: "prod-1", name: "Fan-out Prod" }],
  }),
}));

// ProjectSelector: a button that sets the project, so the test can populate
// projectId without driving a data-fetching combobox.
vi.mock("@/components/projects/project-selector", () => ({
  ProjectSelector: ({ onChange }: { onChange: (v: string | null) => void }) => (
    <button type="button" onClick={() => onChange("proj-1")}>
      Set Project
    </button>
  ),
}));

// AcceptanceCriteriaEditor: a button that supplies one criterion, bypassing
// the multi-input editor so the AC validation passes.
vi.mock("../acceptance-criteria-editor", () => ({
  AcceptanceCriteriaEditor: ({
    onChange,
  }: {
    onChange: (c: string[]) => void;
  }) => (
    <button type="button" onClick={() => onChange(["criterion one"])}>
      Add Criterion
    </button>
  ),
}));

// MarkdownEditor: a controlled textarea so the test can type a >=20-char desc.
vi.mock("../markdown-editor", () => ({
  MarkdownEditor: ({
    value,
    onChange,
    label,
  }: {
    value: string;
    onChange: (v: string) => void;
    label: string;
  }) => (
    <label>
      {label}
      <textarea
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  ),
}));

// DependencySelector / TaskSelector / AgentSelector: optional, render nothing.
vi.mock("../dependency-selector", () => ({
  DependencySelector: () => null,
}));
vi.mock("../task-selector", () => ({ TaskSelector: () => null }));
vi.mock("@/components/agents/agent-selector", () => ({
  AgentSelector: () => null,
}));

// Collapsible: render the advanced section open so the Project/Product fields
// are reachable without a pointer-driven toggle.
vi.mock("@/components/ui/collapsible", () => ({
  Collapsible: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  CollapsibleTrigger: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  CollapsibleContent: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));

// Select: a native <select> stub so the Product picker (a radix Select) can be
// driven with fireEvent.change. All the dialog's selects become native ones;
// they carry their own default values, so only the Product select needs action.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value: string;
    onValueChange?: (v: string) => void;
    children: React.ReactNode;
  }) => (
    <select value={value} onChange={(e) => onValueChange?.(e.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  SelectValue: () => null,
  SelectContent: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  SelectItem: ({
    value,
    children,
  }: {
    value: string;
    children: React.ReactNode;
  }) => <option value={value}>{children}</option>,
}));

import { CreateTaskDialog } from "../create-task-dialog";

function fillBasics() {
  fireEvent.change(screen.getByPlaceholderText(/Enter task title/i), {
    target: { value: "A task title" },
  });
  fireEvent.change(screen.getByLabelText("Description"), {
    target: {
      value: "A description long enough to pass the twenty char minimum.",
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add Criterion" }));
}

// The Product select is the only native <select> offering the prod-1 option.
// radix Dialog portals content to document.body, so query the document (not
// the render container).
function productSelect() {
  const selects = Array.from(document.querySelectorAll("select"));
  return selects.find((s) => s.querySelector('option[value="prod-1"]'))!;
}

function submit() {
  fireEvent.submit(document.querySelector("form")!);
}

describe("CreateTaskDialog — project/product mutual exclusivity (F085)", () => {
  beforeEach(() => {
    mutateAsync.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("rejects submitting both a Project and a Product", async () => {
    render(<CreateTaskDialog />);
    // Open the dialog (DialogTrigger renders a "New Task" button).
    fireEvent.click(screen.getByRole("button", { name: /New Task/i }));

    fillBasics();
    fireEvent.click(screen.getByRole("button", { name: "Set Project" }));
    fireEvent.change(productSelect(), { target: { value: "prod-1" } });

    submit();

    // A task targets ONE repo OR fans out via a Product — never both. The
    // server silently lets product_id win and drops project_id, so the panel
    // must refuse the ambiguous submit. Before the fix validate() only checked
    // "at least one", so both sailed through to mutateAsync.
    await waitFor(() => {
      expect(mutateAsync).not.toHaveBeenCalled();
    });
    expect(
      screen.getByText(/either a project or a product/i),
    ).toBeInTheDocument();
  });

  it("submits with project_id only when just a Project is picked", async () => {
    render(<CreateTaskDialog />);
    fireEvent.click(screen.getByRole("button", { name: /New Task/i }));

    fillBasics();
    fireEvent.click(screen.getByRole("button", { name: "Set Project" }));

    submit();

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const payload = mutateAsync.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.project_id).toBe("proj-1");
    expect(payload.product_id).toBeUndefined();
  });

  it("submits with product_id only when just a Product is picked", async () => {
    render(<CreateTaskDialog />);
    fireEvent.click(screen.getByRole("button", { name: /New Task/i }));

    fillBasics();
    fireEvent.change(productSelect(), { target: { value: "prod-1" } });

    submit();

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const payload = mutateAsync.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.product_id).toBe("prod-1");
    expect(payload.project_id).toBeUndefined();
  });
});

describe("CreateTaskDialog — Task Type tooltip (W9-5 follow-up)", () => {
  it("explains what the selected task type produces on hover", async () => {
    const user = userEvent.setup();
    render(<CreateTaskDialog />);
    fireEvent.click(screen.getByRole("button", { name: /New Task/i }));

    // Task Type defaults to CODE; the Collapsible mock renders Advanced
    // Options open, so the field is reachable without a pointer toggle.
    await user.hover(screen.getByText("Task Type"));

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /source code changes/i,
    );
  });
});
