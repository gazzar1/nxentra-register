/**
 * A137 — Account Inquiry page (read-only GL drilldown) — Component Tests.
 *
 * Verifies the header, summary cards, transaction rows (debit/credit/running
 * balance), dimension chips, date-filter query params, empty + error states,
 * and the absence of any edit/post/reconcile actions (read-only feature).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// ── Mocks ────────────────────────────────────────────────────────
vi.mock("next-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback || key.split(".").pop() || key,
  }),
}));

vi.mock("next-i18next/serverSideTranslations", () => ({
  serverSideTranslations: vi.fn().mockResolvedValue({}),
}));

vi.mock("next/router", () => ({
  useRouter: () => ({
    push: vi.fn(),
    pathname: "/accounting/chart-of-accounts/[code]/inquiry",
    query: { code: "11510" },
    locale: "en",
  }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, name: "Test", email: "test@test.com" },
    company: { id: 1, name: "Test Co", default_currency: "USD" },
  }),
}));

vi.mock("@/hooks/useCompanyFormat", () => ({
  useCompanyFormat: () => ({
    formatCurrency: (v: string) => `$${v}`,
    formatAmount: (v: string) => v,
    formatDate: (v: string) => v,
    dateFormat: "YYYY-MM-DD",
  }),
}));

vi.mock("@/components/layout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const mockUseInquiry = vi.fn();
vi.mock("@/queries/useAccountInquiry", () => ({
  useAccountInquiry: (...args: unknown[]) => mockUseInquiry(...args),
}));

const mockData = {
  account: {
    public_id: "a1",
    code: "11510",
    name: "Stripe Clearing",
    type: "ASSET",
    normal_side: "DEBIT",
    currency: "USD",
  },
  period: {
    date_from: null,
    date_to: null,
    posted_only: true,
    dimension_type: null,
    dimension_value: null,
    source_module: null,
  },
  summary: {
    opening_balance: "0.00",
    opening_balance_side: "DEBIT",
    period_debits: "150.00",
    period_debits_side: "DEBIT",
    period_credits: "30.00",
    period_credits_side: "CREDIT",
    closing_balance: "120.00",
    closing_balance_side: "DEBIT",
  },
  rows: [
    {
      date: "2026-01-05",
      journal_entry_public_id: "je1",
      journal_entry_number: "JE-0001",
      description: "Stripe payout",
      source_module: "stripe_connector",
      source_document: "po_123",
      counterparty: "",
      debit: "100.00",
      credit: "0.00",
      running_balance: "100.00",
      running_balance_side: "DEBIT",
      dimensions: [
        { type: "SETTLEMENT_PROVIDER", label: "Provider", value: "STRIPE", display: "Stripe" },
      ],
    },
    {
      date: "2026-01-20",
      journal_entry_public_id: "je2",
      journal_entry_number: "JE-0002",
      description: "Bank sweep",
      source_module: "",
      source_document: "",
      counterparty: "",
      debit: "0.00",
      credit: "30.00",
      running_balance: "70.00",
      running_balance_side: "DEBIT",
      dimensions: [
        { type: "SETTLEMENT_PROVIDER", label: "Provider", value: "STRIPE", display: "Stripe" },
        { type: "STORE", label: "Store", value: "S1", display: "Main" },
        { type: "REGION", label: "Region", value: "EG", display: "Egypt" },
      ],
    },
  ],
  pagination: { page: 1, page_size: 50, count: 2, total_pages: 1 },
};

import AccountInquiryPage from "@/pages/accounting/chart-of-accounts/[code]/inquiry";

describe("AccountInquiryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseInquiry.mockReturnValue({ data: mockData, isLoading: false, isError: false });
  });

  it("renders the account header", () => {
    render(<AccountInquiryPage />);
    expect(screen.getByText(/11510.*Stripe Clearing/)).toBeInTheDocument();
  });

  it("renders the four summary cards with values", () => {
    render(<AccountInquiryPage />);
    expect(screen.getByText("Opening Balance")).toBeInTheDocument();
    expect(screen.getByText("Period Debits")).toBeInTheDocument();
    expect(screen.getByText("Period Credits")).toBeInTheDocument();
    expect(screen.getByText("Closing Balance")).toBeInTheDocument();
    // Closing balance value formatted via mocked formatCurrency ($ prefix).
    expect(screen.getByText("$120.00")).toBeInTheDocument();
    expect(screen.getByText("$150.00")).toBeInTheDocument();
  });

  it("renders rows with debit/credit and running balance", () => {
    render(<AccountInquiryPage />);
    expect(screen.getByText("JE-0001")).toBeInTheDocument();
    expect(screen.getByText("JE-0002")).toBeInTheDocument();
    // row 1 debit AND its running balance both render $100.00.
    expect(screen.getAllByText("$100.00").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("$70.00")).toBeInTheDocument(); // row 2 running balance
    expect(screen.getAllByText("$30.00").length).toBeGreaterThan(0); // row 2 credit
  });

  it("renders dimension chips (first two then +N)", () => {
    render(<AccountInquiryPage />);
    // Row 1 has a single dimension chip.
    expect(screen.getAllByText("Provider: Stripe").length).toBeGreaterThan(0);
    // Row 2 has three dimensions → first two shown + a "+1" toggle.
    expect(screen.getByText("Store: Main")).toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
    expect(screen.queryByText("Region: Egypt")).not.toBeInTheDocument();
    // Expanding reveals the third chip.
    fireEvent.click(screen.getByText("+1"));
    expect(screen.getByText("Region: Egypt")).toBeInTheDocument();
  });

  it("passes the date filter to the query as date_from", async () => {
    render(<AccountInquiryPage />);
    const dateInputs = screen.getAllByPlaceholderText("YYYY-MM-DD");
    fireEvent.change(dateInputs[0], { target: { value: "2026-01-15" } });
    await waitFor(() => {
      expect(mockUseInquiry).toHaveBeenCalledWith(
        "11510",
        expect.objectContaining({ date_from: "2026-01-15", page: 1 })
      );
    });
  });

  it("clears the date filter and drops date_from from the query", async () => {
    render(<AccountInquiryPage />);
    const dateInputs = screen.getAllByPlaceholderText("YYYY-MM-DD");
    fireEvent.change(dateInputs[0], { target: { value: "2026-01-15" } });
    const clear = await screen.findByText("Clear");
    fireEvent.click(clear);
    await waitFor(() => {
      const lastCall = mockUseInquiry.mock.calls[mockUseInquiry.mock.calls.length - 1];
      expect(lastCall[1]).not.toHaveProperty("date_from");
    });
  });

  it("shows an empty state when there are no rows", () => {
    mockUseInquiry.mockReturnValue({
      data: {
        ...mockData,
        rows: [],
        pagination: { page: 1, page_size: 50, count: 0, total_pages: 1 },
      },
      isLoading: false,
      isError: false,
    });
    render(<AccountInquiryPage />);
    expect(
      screen.getByText("No journal lines found for this account in the selected period.")
    ).toBeInTheDocument();
  });

  it("shows an error state when the query fails", () => {
    mockUseInquiry.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<AccountInquiryPage />);
    expect(screen.getByText(/Failed to load account transactions/)).toBeInTheDocument();
  });

  it("renders NO edit/post/reconcile actions (read-only)", () => {
    render(<AccountInquiryPage />);
    expect(screen.queryByRole("button", { name: /reconcile/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^post$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
  });
});
