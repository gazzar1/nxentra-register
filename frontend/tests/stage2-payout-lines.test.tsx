/**
 * PR-D3 — Stage-2 payout ledger rows expand into per-line detail (the absorb
 * of the old standalone Payout Verification page).
 *
 * - Expanding a row lazily GETs /accounting/reconciliation/payout-lines/
 *   keyed provider+batch (never a Stripe-shaped id).
 * - The detail renders the reconciliation outcome, per-line match state, and
 *   money labeled with the payout's own currency (single-payout expansion —
 *   the A143 no-blended-tiles invariant holds by construction).
 * - The verify action posts to the provider connector's endpoint and is only
 *   rendered when the backend says verify_supported.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

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
vi.mock('next-i18next/serverSideTranslations', () => ({
  serverSideTranslations: vi.fn().mockResolvedValue({}),
}));

import apiClient from '@/lib/api-client';
import ReconciliationPage from '@/pages/finance/reconciliation';

const client = vi.mocked(apiClient);

const STAGE2_PAYOUT = {
  provider: 'stripe',
  provider_name: 'Stripe',
  provider_type: 'gateway',
  batch_id: 'po_1',
  payout_date: '2026-06-20',
  gross_amount: '150.00',
  fees: '8.85',
  net_amount: '141.15',
  currency: 'USD',
  status: 'posted',
  settlement_entry_id: 7,
  settlement_entry_number: 'JE-000007',
  clearance_entry_id: null,
  clearance_entry_number: '',
};

const SUMMARY = {
  as_of: '2026-07-05',
  narrative: 'All quiet.',
  money_flow: {
    currency: 'EGP',
    total_sold: '0.00',
    segments: [],
    banked: '0.00',
    aged_over_30d: '0.00',
    balanced: true,
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
    available: true,
    settled_count: 1,
    settled_total: '6774.00',
    payouts: [STAGE2_PAYOUT],
  },
  stage3: { available: false },
  needs_review: {
    items: [],
    unresolved_difference_count: 0,
    unresolved_difference_amount: '0.00',
  },
};

const PAYOUT_LINES = {
  provider: 'stripe',
  batch_id: 'po_1',
  header: {
    reconciliation_outcome: 'discrepancy',
    matched_line_count: 1,
    unmatched_line_count: 1,
    verified_line_count: 1,
    total_line_count: 2,
    gross_variance: '0.00',
    fee_variance: '0.00',
    net_variance: '0.00',
    last_reconciled_at: '2026-07-05T10:00:00+00:00',
    reconciliation_source: 'auto_reconcile',
    currency: 'USD',
    verify_supported: true,
  },
  lines: [
    {
      line_index: 0,
      kind: 'charge',
      source_id: 'ch_1',
      gross_amount: '100.00',
      fee: '5.90',
      net_amount: '94.10',
      uncollected_amount: '0.00',
      currency: 'USD',
      verified: true,
      match_kind: 'charge',
      matched_ref: 'ch_1',
      matched_ref_type: 'charge',
      provider_line_ref: 'txn_1',
      verified_at: '2026-07-05T10:00:00+00:00',
    },
    {
      line_index: 1,
      kind: 'charge',
      source_id: 'ch_2',
      gross_amount: '50.00',
      fee: '2.95',
      net_amount: '47.05',
      uncollected_amount: '0.00',
      currency: 'USD',
      verified: false,
      match_kind: 'none',
      matched_ref: '',
      matched_ref_type: '',
      provider_line_ref: 'txn_2',
      verified_at: null,
    },
  ],
};

function mockApi() {
  // After a verify POST, the payout-lines response advances last_reconciled_at
  // (the projection stamped a new snapshot) — the page's bounded poll keys on
  // exactly that transition.
  let verified = false;
  client.get.mockImplementation((url: string) => {
    if (url === '/accounting/reconciliation/summary/') return Promise.resolve({ data: SUMMARY });
    if (url === '/accounting/reconciliation/payout-lines/') {
      const data = verified
        ? {
            ...PAYOUT_LINES,
            header: {
              ...PAYOUT_LINES.header,
              reconciliation_outcome: 'verified',
              last_reconciled_at: '2026-07-05T11:00:00+00:00',
            },
          }
        : PAYOUT_LINES;
      return Promise.resolve({ data });
    }
    return Promise.resolve({ data: {} });
  });
  client.post.mockImplementation((url: string) => {
    if (url.includes('/verify/')) verified = true;
    return Promise.resolve({ data: {} });
  });
}

describe('Stage-2 payout per-line detail (PR-D3)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi();
  });

  it('payoutLines service is keyed provider+batch', async () => {
    const { reconciliationService } = await import('@/services/reconciliation.service');
    await reconciliationService.payoutLines('stripe', 'po_1');
    expect(client.get).toHaveBeenCalledWith('/accounting/reconciliation/payout-lines/', {
      params: { provider: 'stripe', batch_id: 'po_1' },
    });
  });

  it('expanding a payout row lazily fetches and renders line match state', async () => {
    const user = userEvent.setup();
    render(<ReconciliationPage />);

    const batchCell = await screen.findByText('po_1');
    // No detail fetch before expansion.
    expect(client.get).not.toHaveBeenCalledWith(
      '/accounting/reconciliation/payout-lines/',
      expect.anything()
    );

    await user.click(batchCell);

    expect(await screen.findByText('Discrepancy')).toBeInTheDocument();
    expect(screen.getByText(/1\/2 lines matched/)).toBeInTheDocument();
    expect(screen.getByText(/Matched ch_1/)).toBeInTheDocument();
    expect(screen.getByText('Unmatched')).toBeInTheDocument();
    // Line money is labeled with the payout's own currency.
    expect(screen.getByText('ch_2')).toBeInTheDocument();
  });

  it('verify action posts to the provider connector endpoint', async () => {
    const user = userEvent.setup();
    render(<ReconciliationPage />);

    await user.click(await screen.findByText('po_1'));
    const verifyButton = await screen.findByRole('button', {
      name: /Verify against local records/,
    });
    await user.click(verifyButton);

    await waitFor(() =>
      expect(client.post).toHaveBeenCalledWith('/stripe/payouts/po_1/verify/')
    );
  });

  it('verify button is hidden when the provider has no verify endpoint', async () => {
    client.get.mockImplementation((url: string) => {
      if (url === '/accounting/reconciliation/summary/') return Promise.resolve({ data: SUMMARY });
      if (url === '/accounting/reconciliation/payout-lines/')
        return Promise.resolve({
          data: {
            ...PAYOUT_LINES,
            provider: 'paymob',
            header: { ...PAYOUT_LINES.header, verify_supported: false },
          },
        });
      return Promise.resolve({ data: {} });
    });
    const user = userEvent.setup();
    render(<ReconciliationPage />);

    await user.click(await screen.findByText('po_1'));
    await screen.findByText('Discrepancy');
    expect(screen.queryByRole('button', { name: /Verify against local records/ })).toBeNull();
  });
});
