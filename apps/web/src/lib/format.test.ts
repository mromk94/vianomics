import { describe, expect, it } from "vitest";

import {
  fmtCurrency,
  fmtNum,
  fmtPct,
  relTime,
  signedClass,
} from "./format";

describe("formatters", () => {
  it("renders null as em-dash placeholder, never zero", () => {
    expect(fmtCurrency(null)).toBe("—");
    expect(fmtPct(null)).toBe("—");
    expect(fmtNum(undefined)).toBe("—");
  });

  it("formats currency", () => {
    expect(fmtCurrency(1284550)).toBe("$1,284,550");
    expect(fmtCurrency(42.5)).toBe("$42.50");
  });

  it("formats signed percentages", () => {
    expect(fmtPct(1.24)).toBe("+1.2%");
    expect(fmtPct(-0.42)).toBe("-0.4%");
  });

  it("classifies sign for coloring", () => {
    expect(signedClass(1)).toBe("text-pos");
    expect(signedClass(-1)).toBe("text-neg");
    expect(signedClass(0)).toBe("text-dim");
    expect(signedClass(null)).toBe("text-dim");
  });

  it("relative time", () => {
    expect(relTime(null)).toBe("—");
    expect(relTime(new Date().toISOString())).toBe("just now");
  });
});
