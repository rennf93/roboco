import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AcceptanceCriteriaEditor } from "../acceptance-criteria-editor";

const sevenCriteria = Array.from({ length: 7 }, (_, i) => `Criterion ${i + 1}`);

describe("AcceptanceCriteriaEditor — max-7 guard", () => {
  it("allows adding while under the cap", async () => {
    const onChange = vi.fn();
    render(
      <AcceptanceCriteriaEditor
        criteria={["Criterion 1"]}
        onChange={onChange}
      />,
    );

    await userEvent.type(
      screen.getByPlaceholderText(/enter acceptance criterion/i),
      "Criterion 2",
    );
    await userEvent.click(screen.getByRole("button", { name: /add/i }));

    expect(onChange).toHaveBeenCalledWith(["Criterion 1", "Criterion 2"]);
    // `criteria` is controlled by the parent — unchanged in this render since
    // the test doesn't re-render with the mutation applied.
    expect(screen.getByText("1/7 item")).toBeInTheDocument();
  });

  it("disables the add control and shows the cap hint at 7 criteria", () => {
    const onChange = vi.fn();
    render(
      <AcceptanceCriteriaEditor criteria={sevenCriteria} onChange={onChange} />,
    );

    expect(screen.getByText("7/7 items")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add/i })).toBeDisabled();
    expect(
      screen.getByPlaceholderText(/maximum of 7 criteria reached/i),
    ).toBeDisabled();
    expect(
      screen.getAllByText(/maximum of 7 acceptance criteria reached/i).length,
    ).toBeGreaterThan(0);
  });

  it("never calls onChange for an 8th criterion even via Enter", async () => {
    const onChange = vi.fn();
    render(
      <AcceptanceCriteriaEditor criteria={sevenCriteria} onChange={onChange} />,
    );

    const input = screen.getByPlaceholderText(/maximum of 7 criteria reached/i);
    expect(input).toBeDisabled();
    // A disabled input can't be typed into or submitted — confirms the guard
    // is enforced at the control, not just the handler.
    await userEvent.type(input, "Criterion 8");
    expect(onChange).not.toHaveBeenCalled();
  });
});
