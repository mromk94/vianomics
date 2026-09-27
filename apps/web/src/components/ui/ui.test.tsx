import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "./empty-state";
import { healthTone, severityTone, StatusBadge } from "./status-badge";

describe("StatusBadge", () => {
  it("renders label", () => {
    render(<StatusBadge tone="pos">PASS</StatusBadge>);
    expect(screen.getByText("PASS")).toBeInTheDocument();
  });

  it("maps health statuses to tones", () => {
    expect(healthTone("up")).toBe("pos");
    expect(healthTone("down")).toBe("neg");
    expect(healthTone("degraded")).toBe("warn");
    expect(healthTone("unconfigured")).toBe("info");
  });

  it("maps severities to tones", () => {
    expect(severityTone("critical")).toBe("neg");
    expect(severityTone("warning")).toBe("warn");
    expect(severityTone("info")).toBe("info");
  });
});

describe("EmptyState", () => {
  it("renders title and hint", () => {
    render(<EmptyState title="No data" hint="nothing here yet" />);
    expect(screen.getByText("No data")).toBeInTheDocument();
    expect(screen.getByText("nothing here yet")).toBeInTheDocument();
  });
});
