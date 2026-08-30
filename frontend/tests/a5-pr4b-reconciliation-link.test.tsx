/**
 * A5-PR4b — reconciliation "resolve difference" durable adjustment link.
 *
 * M-30: a successful resolveDifference retains a PERSISTENT link to the
 *       returned journal (not just a toast).
 * M-31: the link uses adjustment_entry_id in the path.
 * M-32: the summary refresh still occurs (the resolved row leaves the queue).
 * M-33: no second manual "Create adjustment" action appears beside
 *       resolveDifference.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

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

const NEEDS_REVIEW_ITEM = {
  kind: 'bank_line_difference' as const,
  bank_line_id: 31,
  bank_line_public_id: 'bl-pub-31',
  line_date: '2026-08-20',
  description: 'STRIPE PAYOUT',
  provider_code: 'stripe',
  batch_id: 'po_9',
  expected: '100.00',
  received: '95.00',
  difference: '5.00',
  difference_direction: 'short_paid' as const,
  age_days: 3,
  available_reasons: [
    { value: 'BANK_CHARGE', label: 'Bank charge' },
    { value: 'ROUNDING', label: 'Rounding' },
  ],
};

const summaryWith = (items: (typeof NEEDS_REVIEW_ITEM)[]) => ({
  as_of: '2026-08-30',
  narrative: 'One deposit needs review.',
  money_flow: {
    currency: 'EGP',
    total_sold: '0.00',
    segments: [],
    banked: '0.00',
    aged_over_30d: '0.00',
    balanced: true,
  },
  matches: {
    total: 1,
    confirmed: 0,
    needs_review: items.length,
    unmatched: 0,
    excluded: 0,
    avg_confidence: null,
    auto_matched: 0,
    manually_matched: 0,
  },
  stage1: {
    providers: [],
    totals: {
      total_expected: '0.00',
      total_settled: '0.00',
      total_refunded: '0.00',
      open_balance: '0.00',
      providers_with_open_balance: 0,
      providers_needing_review: 0,
      aged_30_plus: '0.00',
    },
  },
  stage2: {
    available: false,
    settled_count: 0,
    settled_total: '0.00',
    functional_currency: 'EGP',
    payouts: [],
  },
  stage3: {
    available: false,
    total_lines: 0,
    matched_lines: 0,
    unmatched_lines: 0,
    matched_with_unresolved_difference: items.length,
    unmatched_items: [],
  },
  needs_review: {
    items,
    unresolved_difference_count: items.length,
    unresolved_difference_amount: '5.00',
  },
});

async function resolveTheItem() {
  render(<ReconciliationPage />);
  await screen.findByText(/Needs Review \(1\)/);

  // Pick a reason (native select) and resolve.
  fireEvent.change(screen.getByDisplayValue('Pick a reason…'), {
    target: { value: 'BANK_CHARGE' },
  });
  fireEvent.click(screen.getByText('Resolve'));
  await waitFor(() =>
    expect(client.patch).toHaveBeenCalledWith(
      '/accounting/bank-statements/lines/31/difference/',
      { reason: 'BANK_CHARGE', notes: undefined }
    )
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // First summary GET: the item is pending. After the resolve PATCH, the
  // refreshed summary no longer contains it.
  let resolved = false;
  client.get.mockImplementation((url: string) => {
    if (url === '/accounting/reconciliation/summary/') {
      return Promise.resolve({ data: resolved ? summaryWith([]) : summaryWith([NEEDS_REVIEW_ITEM]) });
    }
    return Promise.resolve({ data: {} });
  });
  client.patch.mockImplementation((url: string) => {
    if (url.includes('/difference/')) {
      resolved = true;
      return Promise.resolve({
        data: { bank_line_id: 31, adjustment_entry_id: 77, adjustment_entry_public_id: 'je-pub-77' },
      });
    }
    return Promise.resolve({ data: {} });
  });
});

describe('A5-PR4b reconciliation adjustment link', () => {
  it('M-30/M-31: success renders a persistent card linking the returned journal by id', async () => {
    await resolveTheItem();

    // The durable success card (not just the toast) with the id-based link.
    const link = await screen.findByText('View journal je-pub-77');
    expect(link.closest('a')!.getAttribute('href')).toBe('/accounting/journal-entries/77');
    expect(screen.getByText(/Difference adjustment posted for batch/)).toBeInTheDocument();
    expect(screen.getByText('po_9')).toBeInTheDocument();

    // Persistent: still present after the toast's lifetime would have passed.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText('View journal je-pub-77')).toBeInTheDocument();

    // Dismiss clears it.
    fireEvent.click(screen.getByLabelText('Dismiss'));
    expect(screen.queryByText('View journal je-pub-77')).toBeNull();
  });

  it('M-32: the summary refresh still runs and the resolved row leaves the queue', async () => {
    await resolveTheItem();

    await screen.findByText('View journal je-pub-77');
    const summaryCalls = (client.get as ReturnType<typeof vi.fn>).mock.calls.filter(
      (c) => c[0] === '/accounting/reconciliation/summary/'
    );
    expect(summaryCalls.length).toBeGreaterThanOrEqual(2);
    await waitFor(() => expect(screen.queryByText(/Needs Review \(1\)/)).toBeNull());
  });

  it('M-33: no second manual Create-adjustment action exists beside resolveDifference', async () => {
    render(<ReconciliationPage />);
    await screen.findByText(/Needs Review \(1\)/);
    expect(screen.queryByText('Create adjustment')).toBeNull();
    // resolveDifference (the Resolve button) is the one correction path.
    expect(screen.getAllByText('Resolve')).toHaveLength(1);
  });
});
