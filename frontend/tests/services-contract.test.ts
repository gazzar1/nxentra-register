/**
 * API Contract Tests
 *
 * Verify that frontend services call the correct backend endpoints
 * with the expected HTTP method and parameters. These tests catch
 * drift between frontend and backend URL patterns.
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
// Auth Service
// ─────────────────────────────────────────────────────────
describe('authService contracts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('login → POST /auth/login/', async () => {
    const { authService } = await import('@/services/auth.service');
    await authService.login({ email: 'a@b.com', password: 'pass' });
    expect(client.post).toHaveBeenCalledWith('/auth/login/', { email: 'a@b.com', password: 'pass' });
  });

  it('getProfile → GET /auth/me/', async () => {
    const { authService } = await import('@/services/auth.service');
    await authService.getProfile();
    expect(client.get).toHaveBeenCalledWith('/auth/me/');
  });

  it('logout → POST /auth/logout/', async () => {
    const { authService } = await import('@/services/auth.service');
    await authService.logout('refresh-token');
    expect(client.post).toHaveBeenCalledWith('/auth/logout/', { refresh: 'refresh-token' });
  });

  it('switchCompany → POST /auth/switch-company/', async () => {
    const { authService } = await import('@/services/auth.service');
    await authService.switchCompany(42);
    expect(client.post).toHaveBeenCalledWith('/auth/switch-company/', { company_id: 42 });
  });
});

// ─────────────────────────────────────────────────────────
// Journal Service
// ─────────────────────────────────────────────────────────
describe('journalService contracts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('list → GET /accounting/journal-entries/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.list({ status: 'POSTED' });
    expect(client.get).toHaveBeenCalledWith('/accounting/journal-entries/', {
      params: { status: 'POSTED' },
    });
  });

  it('get → GET /accounting/journal-entries/:id/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.get(7);
    expect(client.get).toHaveBeenCalledWith('/accounting/journal-entries/7/');
  });

  it('create → POST /accounting/journal-entries/', async () => {
    const { journalService } = await import('@/services/journal.service');
    const payload = { date: '2026-01-01', memo: 'test', lines: [] };
    await journalService.create(payload as any);
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/', payload);
  });

  it('post → POST /accounting/journal-entries/:id/post/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.post(5);
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/5/post/');
  });

  it('reverse → POST /accounting/journal-entries/:id/reverse/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.reverse(5);
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/5/reverse/');
  });

  it('delete → DELETE /accounting/journal-entries/:id/', async () => {
    const { journalService } = await import('@/services/journal.service');
    await journalService.delete(3);
    expect(client.delete).toHaveBeenCalledWith('/accounting/journal-entries/3/');
  });
});

// ─────────────────────────────────────────────────────────
// Reports Service
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
    await reportsService.balanceSheet({ fiscal_year: 2026 });
    expect(client.get).toHaveBeenCalledWith('/reports/balance-sheet/', {
      params: { fiscal_year: 2026 },
    });
  });

  it('incomeStatement → GET /reports/income-statement/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.incomeStatement();
    expect(client.get).toHaveBeenCalledWith('/reports/income-statement/', { params: undefined });
  });

  it('accountBalance → GET /reports/account-balances/:code/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.accountBalance('1000');
    expect(client.get).toHaveBeenCalledWith('/reports/account-balances/1000/');
  });

  it('dashboardCharts → GET /reports/dashboard-charts/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.dashboardCharts(2026);
    expect(client.get).toHaveBeenCalledWith('/reports/dashboard-charts/', {
      params: { fiscal_year: 2026 },
    });
  });

  it('projectionStatus → GET /reports/projection-status/', async () => {
    const { reportsService } = await import('@/services/reports.service');
    await reportsService.projectionStatus();
    expect(client.get).toHaveBeenCalledWith('/reports/projection-status/');
  });
});

// ─────────────────────────────────────────────────────────
// Periods Service
// ─────────────────────────────────────────────────────────
describe('periodsService contracts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('list → GET /reports/periods/', async () => {
    const { periodsService } = await import('@/services/periods.service');
    await periodsService.list(2026);
    expect(client.get).toHaveBeenCalledWith('/reports/periods/', {
      params: { fiscal_year: 2026 },
    });
  });

  it('close → POST /reports/periods/:year/:period/close/', async () => {
    const { periodsService } = await import('@/services/periods.service');
    await periodsService.close(2026, 3);
    expect(client.post).toHaveBeenCalledWith('/reports/periods/2026/3/close/');
  });

  it('open → POST /reports/periods/:year/:period/open/', async () => {
    const { periodsService } = await import('@/services/periods.service');
    await periodsService.open(2026, 3);
    expect(client.post).toHaveBeenCalledWith('/reports/periods/2026/3/open/');
  });

  it('configure → POST /reports/periods/configure/', async () => {
    const { periodsService } = await import('@/services/periods.service');
    await periodsService.configure(2026, 12);
    expect(client.post).toHaveBeenCalledWith('/reports/periods/configure/', {
      fiscal_year: 2026,
      period_count: 12,
    });
  });
});

// ─────────────────────────────────────────────────────────
// Fiscal Year Service
// ─────────────────────────────────────────────────────────
describe('fiscalYearService contracts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('checkCloseReadiness → GET /reports/fiscal-years/:year/close-readiness/', async () => {
    const { fiscalYearService } = await import('@/services/periods.service');
    await fiscalYearService.checkCloseReadiness(2026);
    expect(client.get).toHaveBeenCalledWith('/reports/fiscal-years/2026/close-readiness/');
  });

  it('close → POST /reports/fiscal-years/:year/close/', async () => {
    const { fiscalYearService } = await import('@/services/periods.service');
    await fiscalYearService.close(2026, '3000');
    expect(client.post).toHaveBeenCalledWith('/reports/fiscal-years/2026/close/', {
      retained_earnings_account_code: '3000',
    });
  });

  it('reopen → POST /reports/fiscal-years/:year/reopen/', async () => {
    const { fiscalYearService } = await import('@/services/periods.service');
    await fiscalYearService.reopen(2026, 'Correction needed');
    expect(client.post).toHaveBeenCalledWith('/reports/fiscal-years/2026/reopen/', {
      reason: 'Correction needed',
    });
  });
});
