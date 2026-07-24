import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { READABILITY_CHAR_THRESHOLD } from "@/lib/content-readability";
import { CollapsibleSection } from "../collapsible-section";

// Regression: defaultOpen used to be hardcoded to true regardless of the
// section's content, forcing a long list to render fully expanded. It now
// derives the uncontrolled default from the content-readability spec
// (~10 lines / ~640 chars) unless a caller opts out via an explicit
// defaultOpen/open prop.

describe("CollapsibleSection content-driven default", () => {
  it("defaults open when no content prop is given (back-compat)", () => {
    render(
      <CollapsibleSection title="Section">
        <p>body</p>
      </CollapsibleSection>,
    );
    expect(screen.getByRole("button", { name: "Section" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("defaults open when content is within the readability thresholds", () => {
    render(
      <CollapsibleSection title="Section" content="short body text">
        <p>body</p>
      </CollapsibleSection>,
    );
    expect(screen.getByRole("button", { name: "Section" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("defaults collapsed when content exceeds the readability thresholds", () => {
    render(
      <CollapsibleSection
        title="Section"
        content={"a".repeat(READABILITY_CHAR_THRESHOLD + 1)}
      >
        <p>body</p>
      </CollapsibleSection>,
    );
    expect(screen.getByRole("button", { name: "Section" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("an explicit defaultOpen wins over content-derived collapsing", () => {
    render(
      <CollapsibleSection
        title="Section"
        content={"a".repeat(READABILITY_CHAR_THRESHOLD + 1)}
        defaultOpen={true}
      >
        <p>body</p>
      </CollapsibleSection>,
    );
    expect(screen.getByRole("button", { name: "Section" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("a controlled open prop is unaffected by content length", () => {
    render(
      <CollapsibleSection
        title="Section"
        content={"a".repeat(READABILITY_CHAR_THRESHOLD + 1)}
        open={true}
      >
        <p>body</p>
      </CollapsibleSection>,
    );
    expect(screen.getByRole("button", { name: "Section" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
});
