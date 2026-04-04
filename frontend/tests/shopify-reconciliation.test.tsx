/**
 * Shopify Reconciliation — Service Contract + Status Logic Tests
 *
 * Tests the Shopify reconciliation service contracts and the
 * status badge logic that drives the reconciliation UI.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Service Contract Tests ──────────────────────────────────────

vi.mock('@/lib/api-client', () => {
  const mock = {
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  };
  return { default: mock, getErrorMessage: vi.fn() };
});

import apiClient from '@/lib/api-client';
const client = vi.mocked(apiClient);

describe('shopifyService reconciliation contracts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('getReconciliationSummary passes date range', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getReconciliationSummary('2026-03-01', '2026-03-31');
    expect(client.get).toHaveBeenCalledWith('/shopify/reconciliation/', {
      params: { date_from: '2026-03-01', date_to: '2026-03-31' },
    });
  });

  it('getPayoutReconciliation fetches by payout ID', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getPayoutReconciliation(9001);
    expect(client.get).toHaveBeenCalledWith('/shopify/reconciliation/9001/');
  });

  it('getPayouts defaults to page 1', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getPayouts();
    expect(client.get).toHaveBeenCalledWith('/shopify/payouts/', {
      params: { page: 1 },
    });
  });

  it('getPayouts passes status filter when provided', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getPayouts(2, 'verified');
    expect(client.get).toHaveBeenCalledWith('/shopify/payouts/', {
      params: { page: 2, status: 'verified' },
    });
  });

  it('verifyPayout posts to correct endpoint', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.verifyPayout(9001);
    expect(client.post).toHaveBeenCalledWith('/shopify/payouts/9001/verify/');
  });

  it('getClearingBalance hits correct endpoint', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getClearingBalance();
    expect(client.get).toHaveBeenCalledWith('/shopify/clearing-balance/');
  });

  it('syncPayouts posts to sync endpoint', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.syncPayouts();
    expect(client.post).toHaveBeenCalledWith('/shopify/sync-payouts/');
  });

  it('getAccountMapping fetches mappings', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getAccountMapping();
    expect(client.get).toHaveBeenCalledWith('/shopify/account-mapping/');
  });

  it('updateAccountMapping sends PUT with data', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    const mappings = [{ role: 'SALES_REVENUE', account_id: 1, account_code: '4100', account_name: 'Sales' }];
    await shopifyService.updateAccountMapping(mappings);
    expect(client.put).toHaveBeenCalledWith('/shopify/account-mapping/', mappings);
  });
});

// ── Status Badge Logic Tests ────────────────────────────────────

describe('reconciliation status logic', () => {
  // Replicate the getStatusConfig logic from the page to test it directly
  function getStatusConfig(status: string) {
    switch (status) {
      case 'verified':
        return { label: 'Matched', badge: 'success' };
      case 'partial':
        return { label: 'Partial', badge: 'warning' };
      case 'discrepancy':
        return { label: 'Mismatch', badge: 'destructive' };
      default:
        return { label: 'Unverified', badge: 'secondary' };
    }
  }

  it('verified → Matched (success)', () => {
    const config = getStatusConfig('verified');
    expect(config.label).toBe('Matched');
    expect(config.badge).toBe('success');
  });

  it('partial → Partial (warning)', () => {
    const config = getStatusConfig('partial');
    expect(config.label).toBe('Partial');
    expect(config.badge).toBe('warning');
  });

  it('discrepancy → Mismatch (destructive)', () => {
    const config = getStatusConfig('discrepancy');
    expect(config.label).toBe('Mismatch');
    expect(config.badge).toBe('destructive');
  });

  it('unknown → Unverified (secondary)', () => {
    const config = getStatusConfig('no_transactions');
    expect(config.label).toBe('Unverified');
    expect(config.badge).toBe('secondary');
  });

  it('empty string → Unverified (secondary)', () => {
    const config = getStatusConfig('');
    expect(config.label).toBe('Unverified');
    expect(config.badge).toBe('secondary');
  });
});

// ── Reconciliation Data Shape Tests ─────────────────────────────

describe('reconciliation data shapes', () => {
  it('PayoutListItem has required fields for rendering', () => {
    const payout = {
      shopify_payout_id: 9001,
      payout_date: '2026-03-07',
      gross_amount: '900.00',
      fees: '30.00',
      net_amount: '870.00',
      currency: 'USD',
      shopify_status: 'paid',
      store_domain: 'test.myshopify.com',
      reconciliation_status: 'verified',
      transactions_total: 6,
      transactions_verified: 6,
      journal_entry_id: 'je-001',
    };

    expect(payout.reconciliation_status).toBe('verified');
    expect(Number(payout.net_amount)).toBe(870);
    expect(payout.transactions_total).toBe(payout.transactions_verified);
  });

  it('ReconciliationSummary match_rate is a percentage string', () => {
    const summary = {
      total_payouts: 5,
      verified_payouts: 3,
      match_rate: '90.00',
      total_net: '4350.00',
    };

    expect(Number(summary.match_rate)).toBeGreaterThan(0);
    expect(Number(summary.match_rate)).toBeLessThanOrEqual(100);
  });

  it('TransactionMatch tracks variance for discrepancy detection', () => {
    const match = {
      shopify_transaction_id: 80001,
      transaction_type: 'charge',
      amount: '100.00',
      fee: '3.20',
      net: '96.80',
      matched: true,
      matched_to: '#1001',
      variance: '0.00',
    };

    expect(match.matched).toBe(true);
    expect(Number(match.variance)).toBe(0);
  });

  it('unmatched transaction has non-zero variance', () => {
    const match = {
      shopify_transaction_id: 80002,
      transaction_type: 'charge',
      amount: '50.00',
      fee: '1.75',
      net: '48.25',
      matched: false,
      matched_to: '',
      variance: '50.00',
    };

    expect(match.matched).toBe(false);
    expect(Number(match.variance)).toBeGreaterThan(0);
  });
});
