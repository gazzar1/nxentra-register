/**
 * A5-PR1a — /finance/exceptions fail-closed visibility.
 *
 * Core invariant: missing or failed visibility data must NEVER be presented
 * as an all-clear state. The page tracks a persistent per-source load state
 * (loading/loaded/error/forbidden); the green all-clear requires EVERY source
 * loaded AND every unresolved count zero; a 403 renders access-denied; loaded
 * sibling queues stay usable when one source fails; stale filter responses
 * stay discarded (reset-generation discipline).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const { mockToast, failuresSvc, rejectsSvc, evidenceSvc } = vi.hoisted(() => ({
  mockToast: vi.fn(),
  failuresSvc: { summary: vi.fn(), list: vi.fn(), detail: vi.fn(), resolve: vi.fn() },
  rejectsSvc: { list: vi.fn(), resolve: vi.fn() },
  evidenceSvc: { list: vi.fn(), acknowledge: vi.fn() },
}));

vi.mock("@/components/layout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/common", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));
vi.mock("@/components/ui/toaster", () => ({ useToast: () => ({ toast: mockToast }) }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, name: "Owner", email: "o@test.com", is_staff: false, is_superuser: false },
    membership: { role: "OWNER" },
  }),
}));
vi.mock("next-i18next/serverSideTranslations", () => ({
  serverSideTranslations: vi.fn().mockResolvedValue({}),
}));
vi.mock("@/services/projection-failures.service", () => ({
  projectionFailuresService: failuresSvc,
}));
vi.mock("@/services/import-rejected-rows.service", () => ({
  importRejectedRowsService: rejectsSvc,
}));
vi.mock("@/services/shopify-rejected-evidence.service", () => ({
  shopifyRejectedEvidenceService: evidenceSvc,
}));

import ExceptionsPage from "@/pages/finance/exceptions";

const emptySummary = { total_unresolved: 0, by_projection: [], by_category: [] };
const emptyOffsetList = { results: [], total_count: 0, limit: 100, offset: 0 };
const emptyKeysetList = { results: [], total_count: 0, limit: 100, next_cursor: null };

const forbiddenError = () =>
  Object.assign(new Error("Forbidden"), { response: { status: 403, data: { detail: "denied" } } });

const sampleReject = {
  id: 11,
  public_id: "pid-11",
  source_kind: "SETTLEMENT",
  provider_code: "paymob",
  source_filename: "payout.csv",
  import_batch_id: "batch-1",
  row_index: 4,
  reason_code: "MALFORMED_NUMERIC",
  reason_message: "gross 'abc' is not a number",
  status: "REJECTED",
  raw_row: { gross: "abc" },
  occurrence_count: 1,
  first_seen_at: "2026-08-27T00:00:00Z",
  last_seen_at: "2026-08-27T00:00:00Z",
  resolved: false,
  resolved_at: null,
  resolved_by_id: null,
  resolution_note: "",
};

function mockAllHealthy() {
  failuresSvc.summary.mockResolvedValue(emptySummary);
  failuresSvc.list.mockResolvedValue(emptyOffsetList);
  rejectsSvc.list.mockResolvedValue(emptyKeysetList);
  evidenceSvc.list.mockResolvedValue(emptyKeysetList);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("A5-PR1a exceptions page fail-closed visibility", () => {
  it("J-5: a fully loaded all-zero state renders the genuine all-clear", async () => {
    mockAllHealthy();
    render(<ExceptionsPage />);

    await waitFor(() =>
      expect(screen.getByText(/All projections are running cleanly/)).toBeTruthy()
    );
    expect(screen.getByText(/All clear — projections healthy\./)).toBeTruthy();
    expect(screen.queryByText(/Exception visibility is incomplete/)).toBeNull();
  });

  it("J-1: one failed endpoint can never produce an all-clear", async () => {
    failuresSvc.summary.mockResolvedValue(emptySummary);
    failuresSvc.list.mockResolvedValue(emptyOffsetList);
    rejectsSvc.list.mockResolvedValue(emptyKeysetList);
    evidenceSvc.list.mockRejectedValue(new Error("backend 500"));

    render(<ExceptionsPage />);

    await waitFor(() =>
      expect(screen.getByText(/Exception visibility is incomplete/)).toBeTruthy()
    );
    // The green all-clear and the "All clear" total text must NOT render.
    expect(screen.queryByText(/All projections are running cleanly/)).toBeNull();
    expect(screen.queryByText(/All clear — projections healthy\./)).toBeNull();
    expect(screen.getByText(/Unavailable — some sources failed to load\./)).toBeTruthy();
    // The failed queue names itself and does not show an empty/zero state.
    expect(
      screen.getByText(/Rejected Shopify payloads failed to load, so their state is unknown/)
    ).toBeTruthy();
    expect(screen.queryByText(/No rejected Shopify order\/refund payloads\./)).toBeNull();
  });

  it("J-2: the incomplete-visibility state is persistent page state, not a toast", async () => {
    failuresSvc.summary.mockResolvedValue(emptySummary);
    failuresSvc.list.mockResolvedValue(emptyOffsetList);
    rejectsSvc.list.mockRejectedValue(new Error("network down"));
    evidenceSvc.list.mockResolvedValue(emptyKeysetList);

    render(<ExceptionsPage />);

    await waitFor(() =>
      expect(screen.getByText(/Exception visibility is incomplete/)).toBeTruthy()
    );
    // The toast fired once as a secondary signal, but the banner is rendered
    // page state — still present after the toast's lifetime would have passed.
    expect(mockToast).toHaveBeenCalled();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText(/Exception visibility is incomplete/)).toBeTruthy();
    expect(screen.getByText(/Failed to load: Import rejections/)).toBeTruthy();
  });

  it("J-3: loaded sibling queues remain visible when one source fails", async () => {
    failuresSvc.summary.mockResolvedValue(emptySummary);
    failuresSvc.list.mockResolvedValue(emptyOffsetList);
    rejectsSvc.list.mockResolvedValue({
      results: [sampleReject],
      total_count: 1,
      limit: 100,
      next_cursor: null,
    });
    evidenceSvc.list.mockRejectedValue(new Error("boom"));

    render(<ExceptionsPage />);

    await waitFor(() =>
      expect(screen.getByText(/Exception visibility is incomplete/)).toBeTruthy()
    );
    // The loaded reject row is fully usable...
    expect(screen.getByText(/gross 'abc' is not a number/)).toBeTruthy();
    expect(screen.getByText("MALFORMED_NUMERIC")).toBeTruthy();
    // ...while the failed queue shows its unknown-state error, not an empty state.
    expect(
      screen.getByText(/Rejected Shopify payloads failed to load, so their state is unknown/)
    ).toBeTruthy();
  });

  it("J-4: a 403 renders access denied — never an empty queue or all-clear", async () => {
    failuresSvc.summary.mockRejectedValue(forbiddenError());
    failuresSvc.list.mockRejectedValue(forbiddenError());
    rejectsSvc.list.mockRejectedValue(forbiddenError());
    evidenceSvc.list.mockRejectedValue(forbiddenError());

    render(<ExceptionsPage />);

    await waitFor(() => expect(screen.getByText("Access denied")).toBeTruthy());
    expect(screen.getByText(/does not have the/)).toBeTruthy();
    expect(screen.queryByText(/All projections are running cleanly/)).toBeNull();
    expect(screen.queryByText(/No dropped settlement\/bank import rows\./)).toBeNull();
    expect(screen.queryByText(/No rejected Shopify order\/refund payloads\./)).toBeNull();
  });

  it("J-6: a stale filter response is discarded, not rendered", async () => {
    failuresSvc.summary.mockResolvedValue(emptySummary);
    rejectsSvc.list.mockResolvedValue(emptyKeysetList);
    evidenceSvc.list.mockResolvedValue(emptyKeysetList);

    const failureRow = (id: number, message: string) => ({
      id,
      projection_name: "shopify_accounting",
      event_id: `ev-${id}`,
      event_type: "shopify.order_paid",
      category: "MISSING_CONFIG" as const,
      category_display: "Missing config",
      message,
      fix_hint: "",
      occurrence_count: 1,
      first_seen_at: "2026-08-27T00:00:00Z",
      last_seen_at: "2026-08-27T00:00:00Z",
      resolved: false,
      resolved_at: null,
      resolved_by_id: null,
      resolved_by_name: null,
      resolution_note: "",
    });

    // First (gen-1) list call: a SLOW response we resolve LAST, with OLD data.
    // Second (gen-2, after the filter change) resolves first with NEW data.
    let resolveOld!: (v: unknown) => void;
    const oldPromise = new Promise((r) => {
      resolveOld = r;
    });
    failuresSvc.list
      .mockImplementationOnce(() => oldPromise)
      .mockImplementationOnce(() =>
        Promise.resolve({
          results: [failureRow(2, "NEW-GENERATION failure row")],
          total_count: 1,
          limit: 100,
          offset: 0,
        })
      );

    render(<ExceptionsPage />);

    // Change the status filter while gen-1 is still in flight.
    const statusSelect = screen.getAllByRole("combobox")[0];
    fireEvent.change(statusSelect, { target: { value: "all" } });

    await waitFor(() => expect(screen.getByText(/NEW-GENERATION failure row/)).toBeTruthy());

    // Now the stale gen-1 response lands — it must be discarded.
    resolveOld({
      results: [failureRow(1, "STALE-GENERATION failure row")],
      total_count: 1,
      limit: 100,
      offset: 0,
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/STALE-GENERATION failure row/)).toBeNull();
    expect(screen.getByText(/NEW-GENERATION failure row/)).toBeTruthy();
  });
});
