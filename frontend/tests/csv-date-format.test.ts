/**
 * A128 — the bank-import CSV date sniffer must NOT silently guess DD/MM vs
 * MM/DD. It scans every sample row; a component > 12 pins the order, and when
 * no sample disambiguates it returns an empty format + ambiguous=true so the
 * mapping dialog forces an explicit pick instead of transposing day/month.
 */
import { describe, it, expect } from "vitest";
import { detectDateFormat, suggestMapping } from "@/components/common/CsvMappingDialog";

describe("detectDateFormat", () => {
  it("returns year-first ISO shapes unambiguously", () => {
    expect(detectDateFormat(["2026-04-01", "2026-04-02"])).toEqual({
      format: "%Y-%m-%d",
      ambiguous: false,
    });
    expect(detectDateFormat(["2026/04/01"])).toEqual({ format: "%Y/%m/%d", ambiguous: false });
  });

  it("flags genuinely ambiguous slash dates instead of defaulting to DD/MM", () => {
    const r = detectDateFormat(["03/04/2026", "05/06/2026"]);
    expect(r).toEqual({ format: "", ambiguous: true });
  });

  it("detects DD/MM when any sample has the first field > 12", () => {
    expect(detectDateFormat(["13/04/2026", "05/06/2026"])).toEqual({
      format: "%d/%m/%Y",
      ambiguous: false,
    });
  });

  it("detects MM/DD when any sample has the second field > 12", () => {
    expect(detectDateFormat(["04/13/2026", "05/06/2026"])).toEqual({
      format: "%m/%d/%Y",
      ambiguous: false,
    });
  });

  it("handles dash separators in both orders", () => {
    expect(detectDateFormat(["13-04-2026"]).format).toBe("%d-%m-%Y");
    expect(detectDateFormat(["04-13-2026"]).format).toBe("%m-%d-%Y");
  });

  it("treats contradictory evidence (one row day-first, another month-first) as ambiguous", () => {
    expect(detectDateFormat(["13/04/2026", "04/13/2026"]).ambiguous).toBe(true);
  });

  it("defaults to ISO and does not block when there are no usable samples", () => {
    expect(detectDateFormat([])).toEqual({ format: "%Y-%m-%d", ambiguous: false });
    expect(detectDateFormat(["", "  "])).toEqual({ format: "%Y-%m-%d", ambiguous: false });
  });
});

describe("suggestMapping date_format", () => {
  const headers = ["Date", "Description", "Amount"];

  it("leaves date_format empty for an ambiguous file so the dialog forces a pick", () => {
    const rows = [
      { Date: "03/04/2026", Description: "x", Amount: "10" },
      { Date: "05/06/2026", Description: "y", Amount: "20" },
    ];
    expect(suggestMapping(headers, rows).date_format).toBe("");
  });

  it("picks the confident format when the data disambiguates", () => {
    const rows = [{ Date: "13/04/2026", Description: "x", Amount: "10" }];
    expect(suggestMapping(headers, rows).date_format).toBe("%d/%m/%Y");
  });
});
