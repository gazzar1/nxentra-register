/**
 * A143 — Stripe dashboard tiles read canonical payout fees.
 *
 * The old dashboard summed charge-side `fee` client-side, which is 0 by
 * design (webhooks carry no fee — real fees only become known at payout
 * time). The tiles now come from GET /stripe/summary/, grouped per currency.
 *
 * PR-D3 note: the standalone Payout Verification page (and its A143
 * mixed-currency tile tests) was absorbed into /finance/reconciliation
 * Stage-2 — there every money value is labeled with its own payout/line
 * currency by construction (single-payout expansions, no blended tiles);
 * see stage2-payout-lines.test.tsx.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

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
vi.mock('next-i18next/serverSideTranslations', () => ({
  serverSideTranslations: vi.fn().mockResolvedValue({}),
}));
// Mirror the real hook's contract: no currency arg → company default (EGP
// here, the artifact-41 shape where books ≠ Stripe payout currency).
vi.mock('@/hooks/useCompanyFormat', () => ({
  useCompanyFormat: () => ({
    formatCurrency: (v: number | string, cur?: string) => `${cur ?? 'EGP'} ${Number(v).toFixed(2)}`,
    formatAmount: (v: number | string) => Number(v).toFixed(2),
    formatDate: (d: string) => d,
  }),
}));

import apiClient from '@/lib/api-client';
import StripeDashboardPage from '@/pages/stripe/index';

const client = vi.mocked(apiClient);

const CONNECTED_ACCOUNT = {
  connected: true,
  status: 'ACTIVE',
  display_name: 'Acme Stripe',
  stripe_account_id: 'acct_1',
  livemode: false,
  webhook_secret_configured: true,
  created_at: '2026-06-01T00:00:00Z',
};

// Charge rows carry fee "0.00" — the exact shape that made the old
// client-side fees tile render a false 0.
const CHARGES = [
  {
    id: 1,
    public_id: 'c1',
    stripe_charge_id: 'ch_1',
    amount: '100.00',
    fee: '0.00',
    net: '100.00',
    currency: 'USD',
    description: 'Order 1',
    customer_email: '',
    customer_name: '',
    charge_date: '2026-06-15',
    status: 'PROCESSED',
    journal_entry_id: null,
    created_at: '2026-06-15T00:00:00Z',
  },
  {
    id: 2,
    public_id: 'c2',
    stripe_charge_id: 'ch_2',
    amount: '50.00',
    fee: '0.00',
    net: '50.00',
    currency: 'USD',
    description: 'Order 2',
    customer_email: '',
    customer_name: '',
    charge_date: '2026-06-16',
    status: 'PROCESSED',
    journal_entry_id: null,
    created_at: '2026-06-16T00:00:00Z',
  },
];

describe('Stripe dashboard tiles (A143)', () => {
  beforeEach(() => vi.clearAllMocks());

  function mockDashboard(summary: unknown | Error) {
    client.get.mockImplementation((url: string) => {
      if (url === '/stripe/account/') return Promise.resolve({ data: CONNECTED_ACCOUNT });
      if (url === '/stripe/charges/') return Promise.resolve({ data: CHARGES });
      if (url === '/stripe/summary/') {
        return summary instanceof Error
          ? Promise.reject(summary)
          : Promise.resolve({ data: summary });
      }
      return Promise.resolve({ data: {} });
    });
  }

  it('getDashboardSummary() → GET /stripe/summary/', async () => {
    const { stripeService } = await import('@/services/stripe.service');
    await stripeService.getDashboardSummary();
    expect(client.get).toHaveBeenCalledWith('/stripe/summary/');
  });

  it('fees tile renders canonical payout fees (not the charge-side 0) + captions', async () => {
    mockDashboard({
      charges: {
        total: 2,
        processed: 2,
        errors: 0,
        revenue: [{ currency: 'USD', amount: '150.00' }],
      },
      fees: [{ currency: 'USD', amount: '9.60', payouts: 2 }],
    });
    render(<StripeDashboardPage />);

    expect(await screen.findByText('USD 9.60')).toBeInTheDocument();
    // Revenue from the server aggregate, labeled with its own currency.
    // (Two nodes: the tile and the two USD 150.00-adjacent charge rows differ,
    // so scope to existence.)
    expect(screen.getAllByText('USD 150.00').length).toBeGreaterThan(0);
    // The captions that stop "Revenue − Fees" mental math from reading as a bug.
    expect(screen.getByText(/Gross charge volume, before fees/)).toBeInTheDocument();
    expect(screen.getByText(/Actual fees from Stripe payout reports/)).toBeInTheDocument();
    // The false 0 must be gone.
    expect(screen.queryByText('USD 0.00')).not.toBeInTheDocument();
  });

  it('multi-currency fees are listed per currency, never blended', async () => {
    mockDashboard({
      charges: { total: 2, processed: 2, errors: 0, revenue: [{ currency: 'USD', amount: '150.00' }] },
      fees: [
        { currency: 'EUR', amount: '2.10', payouts: 1 },
        { currency: 'USD', amount: '6.40', payouts: 1 },
      ],
    });
    render(<StripeDashboardPage />);

    expect(await screen.findByText('EUR 2.10')).toBeInTheDocument();
    expect(screen.getByText('USD 6.40')).toBeInTheDocument();
  });

  it('summary endpoint unavailable → fees show a dash (no false 0), counts fall back to charges', async () => {
    mockDashboard(new Error('boom'));
    render(<StripeDashboardPage />);

    // Fallback revenue derives from the charge rows (100 + 50 USD).
    expect(await screen.findByText('USD 150.00')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('EGP 0.00')).not.toBeInTheDocument();
  });
});

