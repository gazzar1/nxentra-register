/**
 * Accounting Service Contract Tests
 *
 * Verifies that accounting-related frontend services call the correct
 * backend API endpoints. These catch URL/method drift between frontend
 * and backend.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock apiClient before importing services
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

// ─────────────────────────────────────────────────────────
// Journal Service — Full lifecycle
// ─────────────────────────────────────────────────────────
describe('journalService contracts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('list → GET /accounting/journal-entries/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.list({ status: 'POSTED', page: 1, page_size: 25 });
    expect(client.get).toHaveBeenCalledWith('/accounting/journal-entries/', {
      params: { status: 'POSTED', page: 1, page_size: 25 },
    });
  });

  it('get → GET /accounting/journal-entries/{id}/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.get(42);
    expect(client.get).toHaveBeenCalledWith('/accounting/journal-entries/42/');
  });

  it('create → POST /accounting/journal-entries/', async () => {
    const { journalService } = await import('@/services/journal.service');
    const payload = { date: '2026-03-15', memo: 'Test', lines: [] };
    await journalService.create(payload as any);
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/', payload);
  });

  it('saveComplete → PUT /accounting/journal-entries/{id}/complete/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.saveComplete(42, {} as any);
    expect(client.put).toHaveBeenCalledWith('/accounting/journal-entries/42/complete/', {});
  });

  it('post → POST /accounting/journal-entries/{id}/post/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.post(42);
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/42/post/');
  });

  it('reverse → POST /accounting/journal-entries/{id}/reverse/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.reverse(42);
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/42/reverse/');
  });

  it('delete → DELETE /accounting/journal-entries/{id}/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.delete(42);
    expect(client.delete).toHaveBeenCalledWith('/accounting/journal-entries/42/');
  });
});

// ─────────────────────────────────────────────────────────
// Reports Service — Financial statements
// ─────────────────────────────────────────────────────────
describe('reportsService contracts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('trialBalance → GET /reports/trial-balance/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.trialBalance();
    expect(client.get).toHaveBeenCalledWith('/reports/trial-balance/', { params: undefined });
  });

  it('balanceSheet → GET /reports/balance-sheet/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.balanceSheet();
    expect(client.get).toHaveBeenCalledWith('/reports/balance-sheet/', { params: undefined });
  });

  it('incomeStatement → GET /reports/income-statement/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.incomeStatement();
    expect(client.get).toHaveBeenCalledWith('/reports/income-statement/', { params: undefined });
  });

  it('arAging → GET /reports/ar-aging/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.arAging({ as_of: '2026-03-31' });
    expect(client.get).toHaveBeenCalledWith('/reports/ar-aging/', {
      params: { as_of: '2026-03-31' },
    });
  });

  it('apAging → GET /reports/ap-aging/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.apAging({ as_of: '2026-03-31' });
    expect(client.get).toHaveBeenCalledWith('/reports/ap-aging/', {
      params: { as_of: '2026-03-31' },
    });
  });

  it('dashboardCharts → GET /reports/dashboard-charts/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.dashboardCharts();
    expect(client.get).toHaveBeenCalledWith('/reports/dashboard-charts/', {
      params: undefined,
    });
  });

  it('dashboardWidgets → GET /reports/dashboard-widgets/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.dashboardWidgets();
    expect(client.get).toHaveBeenCalledWith('/reports/dashboard-widgets/');
  });
});

// ─────────────────────────────────────────────────────────
// Shopify Service — Reconciliation endpoints
// ─────────────────────────────────────────────────────────
describe('shopifyService contracts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('getPayouts → GET /shopify/payouts/', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getPayouts(1);
    expect(client.get).toHaveBeenCalledWith('/shopify/payouts/', {
      params: { page: 1 },
    });
  });

  it('getPayouts with status filter', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getPayouts(2, 'verified');
    expect(client.get).toHaveBeenCalledWith('/shopify/payouts/', {
      params: { page: 2, status: 'verified' },
    });
  });

  it('getReconciliationSummary → GET /shopify/reconciliation/', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getReconciliationSummary('2026-03-01', '2026-03-31');
    expect(client.get).toHaveBeenCalledWith('/shopify/reconciliation/', {
      params: { date_from: '2026-03-01', date_to: '2026-03-31' },
    });
  });

  it('getPayoutReconciliation → GET /shopify/reconciliation/{id}/', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getPayoutReconciliation(9001);
    expect(client.get).toHaveBeenCalledWith('/shopify/reconciliation/9001/');
  });

  it('getStore → GET /shopify/store/', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getStore();
    expect(client.get).toHaveBeenCalledWith('/shopify/store/');
  });

  it('syncPayouts → POST /shopify/sync-payouts/', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.syncPayouts();
    expect(client.post).toHaveBeenCalledWith('/shopify/sync-payouts/');
  });

  it('verifyPayout → POST /shopify/payouts/{id}/verify/', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.verifyPayout(9001);
    expect(client.post).toHaveBeenCalledWith('/shopify/payouts/9001/verify/');
  });

  it('getClearingBalance → GET /shopify/clearing-balance/', async () => {
    const { shopifyService } = await import('@/services/shopify.service');
    await shopifyService.getClearingBalance();
    expect(client.get).toHaveBeenCalledWith('/shopify/clearing-balance/');
  });
});

// ─────────────────────────────────────────────────────────
// Onboarding Service — Shopify-first flow
// ─────────────────────────────────────────────────────────
describe('onboardingService contracts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('getStatus → GET /onboarding/setup/', async () => {
    const { onboardingService } = await import('@/services/onboarding.service');
    await onboardingService.getStatus();
    expect(client.get).toHaveBeenCalledWith('/onboarding/setup/');
  });

  it('complete → POST /onboarding/setup/ with business_type', async () => {
    const { onboardingService } = await import('@/services/onboarding.service');
    const payload = {
      business_type: 'shopify',
      coa_template: 'retail',
      modules: [{ key: 'shopify_connector', is_enabled: true }],
    };
    await onboardingService.complete(payload);
    expect(client.post).toHaveBeenCalledWith('/onboarding/setup/', payload);
  });
});
