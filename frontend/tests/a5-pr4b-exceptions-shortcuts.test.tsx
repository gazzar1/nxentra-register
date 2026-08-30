/**
 * A5-PR4b — /finance/exceptions "Create adjustment" shortcuts.
 *
 * M-24: a projection failure builds projection_failure:<event_id> — never the
 *       integer failure-row id.
 * M-25: an import reject uses its public_id.
 * M-26: a Shopify reject uses its public_id.
 * M-27: the shortcut is hidden without journal.create.
 * M-28: the shortcut is navigation-only — no resolve/acknowledge call.
 * (+ the shortcut renders for historical/resolved rows too, and the PR #131
 *  fail-closed loading behavior is exercised unchanged by the sibling suite.)
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const { permState, failuresSvc, rejectsSvc, evidenceSvc } = vi.hoisted(() => ({
  permState: { canCreateJournal: true, pilotProfile: "ISOLATED_SHADOW_LEDGER_V1" as string },
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
vi.mock("@/components/ui/toaster", () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, name: "Owner", email: "o@test.com", is_staff: false, is_superuser: false },
    membership: { role: "OWNER" },
    company: { id: 1, name: "Pilot Co", pilot_profile: permState.pilotProfile },
    hasPermission: (code: string) => code === "journal.create" && permState.canCreateJournal,
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

const emptySummary = { total_unresolved: 1, by_projection: [], by_category: [] };
const emptyKeysetList = { results: [], total_count: 0, limit: 100, next_cursor: null };

const failureRow = {
  id: 999, // integer row PK — must NEVER be the adjustment reference
  projection_name: "shopify_accounting",
  event_id: "ev-uuid-0f0e", // the BusinessEvent UUID — the canonical reference
  event_type: "shopify.order_paid",
  category: "MISSING_CONFIG" as const,
  category_display: "Missing config",
  message: "COGS account is not configured",
  fix_hint: "",
  occurrence_count: 1,
  first_seen_at: "2026-08-27T00:00:00Z",
  last_seen_at: "2026-08-27T00:00:00Z",
  resolved: false,
  resolved_at: null,
  resolved_by_id: null,
  resolved_by_name: null,
  resolution_note: "",
};

const rejectRow = {
  id: 11,
  public_id: "pid-import-11",
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

const evidenceRow = {
  id: 3,
  public_id: "pid-shopify-3",
  resource_kind: "ORDER",
  ingress_kind: "WEBHOOK",
  source_topic: "orders/updated",
  shop_domain: "test.myshopify.com",
  store_public_id: "store-1",
  external_id: "5001",
  parent_external_id: null,
  rejection_code: "MALFORMED_MONEY",
  rejection_message: "total_price is not a number",
  validation_errors: [],
  parsed_payload: null,
  raw_body_b64: "",
  payload_hash: "h1",
  transport_hash: "t1",
  occurrence_count: 1,
  first_seen_at: "2026-08-27T00:00:00Z",
  last_seen_at: "2026-08-27T00:00:00Z",
  last_delivery_id: "d-1",
  acknowledged: false,
  acknowledged_at: null,
  acknowledged_by_id: null,
  acknowledgment_note: "",
  superseded_at: null,
  superseded_target_public_id: null,
  redacted_at: null,
};

function mockLoaded({
  failure = failureRow,
  reject = rejectRow,
  evidence = evidenceRow,
}: {
  failure?: typeof failureRow;
  reject?: typeof rejectRow;
  evidence?: typeof evidenceRow;
} = {}) {
  failuresSvc.summary.mockResolvedValue(emptySummary);
  failuresSvc.list.mockResolvedValue({
    results: [failure],
    total_count: 1,
    limit: 100,
    offset: 0,
  });
  rejectsSvc.list.mockResolvedValue({
    results: [reject],
    total_count: 1,
    limit: 100,
    next_cursor: null,
  });
  evidenceSvc.list.mockResolvedValue({
    results: [evidence],
    total_count: 1,
    limit: 100,
    next_cursor: null,
  });
}

const adjustmentLinks = () =>
  screen.getAllByText("Create adjustment").map((el) => el.closest("a")!.getAttribute("href")!);

beforeEach(() => {
  vi.clearAllMocks();
  permState.canCreateJournal = true;
  permState.pilotProfile = "ISOLATED_SHADOW_LEDGER_V1";
});

describe("A5-PR4b exceptions-page adjustment shortcuts", () => {
  it("M-24/25/26: each family links with its canonical reference", async () => {
    mockLoaded();
    render(<ExceptionsPage />);

    await waitFor(() => expect(screen.getAllByText("Create adjustment")).toHaveLength(3));
    const hrefs = adjustmentLinks();

    // Projection failure: event_id, never the integer row id.
    const failureHref = hrefs.find((h) => h.includes("projection_failure"))!;
    expect(failureHref).toContain("adjustment_source_kind=projection_failure");
    expect(failureHref).toContain("adjustment_source_reference=ev-uuid-0f0e");
    expect(failureHref).not.toContain("999");

    // Import reject: public_id.
    const rejectHref = hrefs.find((h) => h.includes("import_reject"))!;
    expect(rejectHref).toContain("adjustment_source_reference=pid-import-11");

    // Shopify reject: public_id.
    const evidenceHref = hrefs.find((h) => h.includes("shopify_reject"))!;
    expect(evidenceHref).toContain("adjustment_source_reference=pid-shopify-3");

    // All three route to the one create page.
    for (const h of hrefs) {
      expect(h.startsWith("/accounting/journal-entries/new?")).toBe(true);
      expect(h).not.toContain("source_module");
      expect(h).not.toContain("source_document");
    }
    // The caveat is on the page: an adjustment is not a repair of the source.
    expect(
      screen.getByText(/does not resolve or repair the source item/)
    ).toBeInTheDocument();
  });

  it("the shortcut also renders for historical/resolved evidence", async () => {
    mockLoaded({
      failure: { ...failureRow, resolved: true, resolved_at: "2026-08-28T00:00:00Z" },
      reject: { ...rejectRow, resolved: true, resolved_at: "2026-08-28T00:00:00Z" },
      evidence: {
        ...evidenceRow,
        acknowledged: true,
        acknowledged_at: "2026-08-28T00:00:00Z",
      },
    });
    render(<ExceptionsPage />);

    await waitFor(() => expect(screen.getAllByText("Create adjustment")).toHaveLength(3));
  });

  it("M-27: without journal.create no shortcut renders", async () => {
    permState.canCreateJournal = false;
    mockLoaded();
    render(<ExceptionsPage />);

    await waitFor(() => expect(screen.getByText(/COGS account is not configured/)).toBeTruthy());
    expect(screen.queryByText("Create adjustment")).toBeNull();
    expect(screen.queryByText(/does not resolve or repair the source item/)).toBeNull();
  });

  it("under profile NONE no shortcut renders — the page is unchanged pre-activation", async () => {
    // The prefill is a pilot-adjustment linkage; under NONE the form deliberately
    // carries no source fields, so offering the link would silently drop the
    // evidence connection the operator thinks they are creating.
    permState.pilotProfile = "NONE";
    mockLoaded();
    render(<ExceptionsPage />);

    await waitFor(() => expect(screen.getByText(/COGS account is not configured/)).toBeTruthy());
    expect(screen.queryByText("Create adjustment")).toBeNull();
    expect(screen.queryByText(/does not resolve or repair the source item/)).toBeNull();
  });

  it("M-28: the shortcut is a link — no resolve/acknowledge call is made", async () => {
    mockLoaded();
    render(<ExceptionsPage />);

    await waitFor(() => expect(screen.getAllByText("Create adjustment")).toHaveLength(3));
    // Rendering (and the links themselves) never touch the mutation endpoints.
    expect(failuresSvc.resolve).not.toHaveBeenCalled();
    expect(rejectsSvc.resolve).not.toHaveBeenCalled();
    expect(evidenceSvc.acknowledge).not.toHaveBeenCalled();
    // And each is a plain anchor (navigation-only, PR #131 loader untouched).
    for (const el of screen.getAllByText("Create adjustment")) {
      expect(el.closest("a")).not.toBeNull();
    }
  });
});
