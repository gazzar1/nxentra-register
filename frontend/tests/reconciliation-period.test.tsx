/**
 * A152 — the /finance/reconciliation period control + roll-forward banner.
 *
 * - On mount the page requests the summary for the default window (This month),
 *   so the endpoint receives ?period=this_month explicitly (the API itself
 *   defaults to all_time when it gets no params).
 * - The structured roll_forward renders as the clickable money identity
 *   (opening + sold − settled − refunded = closing) with each term deep-linked.
 * - A negative-clearing provider surfaces its warning from the Stage-1 stock.
 * - Stock tiles (Open Balance / Aged) are labeled "as of today" when a window
 *   is active, so they don't read as period-scoped.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';

const mockToast = vi.fn();

vi.mock('@/lib/api-client', async (importActual) => {
  const actual = await importActual<typeof import('@/lib/api-client')>();
  return {
    ...actual,
    default: {
      get: vi.fn().mockResolvedValue({ data: {} }),
      post: vi.fn().mockResolvedValue({ data: {} }),
      put: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    },
  };
});
vi.mock('@/components/layout', () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/common', () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock('@/components/ui/toaster', () => ({ useToast: () => ({ toast: mockToast }) }));
vi.mock('@/hooks/useCompanyFormat', () => ({
  useCompanyFormat: () => ({
    dateFormat: 'YYYY-MM-DD',
    formatDate: (v: unknown) => (v == null || v === '' ? '—' : String(v)),
    formatAmount: (v: unknown) => String(v),
    formatCurrency: (v: unknown) => String(v),
    parseAmount: (v: string) => v,
    settings: undefined,
  }),
}));
vi.mock('next-i18next/serverSideTranslations', () => ({
  serverSideTranslations: vi.fn().mockResolvedValue({}),
}));

import apiClient from '@/lib/api-client';
import ReconciliationPage from '@/pages/finance/reconciliation';

const client = vi.mocked(apiClient);

const NEGATIVE_PROVIDER = {
  account_id: 2,
  account_code: '11500',
  account_name: 'Clearing',
  dimension_value_id: 9,
  dimension_value_code: 'BOSTA',
  provider_id: 3,
  provider_name: 'Bosta',
  provider_type: 'courier',
  needs_review: false,
  total_debit: '0.00',
  total_credit: '0.00',
  total_refunded: '0.00',
  banked: '0.00',
  open_balance: '-50.00',
  oldest_entry_date: null,
  days_outstanding: 0,
  aging_bucket: 'none',
  line_count: 1,
};

const SUMMARY = {
  as_of: '2026-07-07',
  period: { preset: 'this_month', start: '2026-07-01', end: '2026-07-31' },
  narrative: 'server narrative (should be superseded by the banner)',
  money_flow: {
    currency: 'EGP',
    total_sold: '0.00', // keep the MoneyBridge out of this focused test
    segments: [],
    banked: '0.00',
    aged_over_30d: '0.00',
    opening_outstanding: '300.00',
    closing_outstanding: '400.00',
    balanced: true,
  },
  roll_forward: {
    opening_outstanding: '300.00',
    sold: '200.00',
    settled: '100.00',
    refunded: '0.00',
    closing_outstanding: '400.00',
    foots: true,
  },
  matches: {
    total: 0,
    confirmed: 0,
    needs_review: 0,
    unmatched: 0,
    excluded: 0,
    avg_confidence: null,
    auto_matched: 0,
    manually_matched: 0,
  },
  stage1: {
    providers: [NEGATIVE_PROVIDER],
    totals: {
      total_expected: '210.00',
      total_settled: '110.00',
      total_refunded: '0.00',
      open_balance: '410.00',
      providers_with_open_balance: 1,
      providers_needing_review: 0,
      aged_30_plus: '0.00',
    },
  },
  stage2: { available: true, settled_count: 1, settled_total: '110.00', functional_currency: 'EGP', payouts: [] },
  stage3: {
    available: true,
    total_lines: 0,
    matched_lines: 0,
    unmatched_lines: 0,
    matched_with_unresolved_difference: 0,
    unmatched_items: [],
  },
  needs_review: { items: [], unresolved_difference_count: 0, unresolved_difference_amount: '0.00' },
};

describe('A152 reconciliation period control + roll-forward banner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.get.mockImplementation((url: string) => {
      if (url === '/accounting/reconciliation/summary/') return Promise.resolve({ data: SUMMARY });
      return Promise.resolve({ data: {} });
    });
  });

  it('requests the default (This month) window on mount', async () => {
    render(<ReconciliationPage />);
    await waitFor(() =>
      expect(client.get).toHaveBeenCalledWith('/accounting/reconciliation/summary/', {
        params: { period: 'this_month' },
      })
    );
  });

  it('renders the roll-forward money identity with clickable deep-linked terms', async () => {
    render(<ReconciliationPage />);

    const label = await screen.findByText(/Where is my money — This month/i);
    // The identity sentence sits alongside the label inside the banner card.
    const banner = label.parentElement as HTMLElement;
    expect(within(banner).getByText(/Opening outstanding/)).toBeInTheDocument();
    expect(within(banner).getByText(/closing outstanding/)).toBeInTheDocument();

    // Each amount is a deep-link to the stage that explains it.
    const opening = within(banner).getByRole('link', { name: '300.00' });
    expect(opening).toHaveAttribute('href', '#stage-1');
    const settled = within(banner).getByRole('link', { name: '100.00' });
    expect(settled).toHaveAttribute('href', '#stage-2');
    const closing = within(banner).getByRole('link', { name: '400.00' });
    expect(closing).toHaveAttribute('href', '#stage-1');
  });

  it('surfaces a negative-clearing warning from the Stage-1 stock', async () => {
    render(<ReconciliationPage />);
    expect(await screen.findByText(/Bosta clearing is negative/)).toBeInTheDocument();
  });

  it('labels stock tiles "as of today" while a window is active', async () => {
    render(<ReconciliationPage />);
    // Both stock tiles (Open Balance + Aged) clarify they are timeless, not windowed.
    const asOfToday = await screen.findAllByText(/as of today/);
    expect(asOfToday.length).toBe(2);
  });
});
