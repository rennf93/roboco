import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const { mutateAsync, push, refresh } = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
}));

vi.mock("@/hooks/use-auth", () => ({
  useLogin: () => ({ mutateAsync, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn() },
}));

import LoginPage from "../page";

describe("LoginPage", () => {
  beforeEach(() => {
    mutateAsync.mockReset();
    push.mockReset();
    refresh.mockReset();
  });

  it("requires both fields before submitting", () => {
    render(<LoginPage />);
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter your email and password",
    );
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("logs in and redirects to /overview on success", async () => {
    mutateAsync.mockResolvedValue(undefined);
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "ceo@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: "hunter2" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        email: "ceo@example.com",
        password: "hunter2",
      });
    });
    expect(push).toHaveBeenCalledWith("/overview");
  });

  it("shows the error message on bad credentials without redirecting", async () => {
    mutateAsync.mockRejectedValue({
      isAxiosError: true,
      response: { status: 400, data: { detail: "LOGIN_BAD_CREDENTIALS" } },
    });
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "ceo@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: "wrong" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "LOGIN_BAD_CREDENTIALS",
      );
    });
    expect(push).not.toHaveBeenCalled();
  });
});
