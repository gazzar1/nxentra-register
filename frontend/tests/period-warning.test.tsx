/**
 * A152 items 5+6 — point-of-entry period feedback + the conditional header nudge.
 *
 * - resolvePeriodLabel: the exact resolve logic lifted from payments/receipts —
 *   "Period N (start — end)", "⚠ CLOSED" flag, "No matching open period".
 * - findPriorOpenPeriod: the chip's actionability rule — a prior NORMAL period
 *   still OPEN and ≥ N days past its end; nothing otherwise (no wallpaper).
 * - CompanyDateInput renders the warning only when showPeriodWarning is set.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import {
  findPriorOpenPeriod,
  resolvePeriodLabel,
  PRIOR_PERIOD_NUDGE_DAYS,
} from '@/queries/usePeriods';
import type { FiscalPeriod } from '@/services/periods.service';

const mockPeriods: { current: FiscalPeriod[] } = { current: [] };

vi.mock('@/queries/usePeriods', async (importActual) => {
  const actual = await importActual<typeof import('@/queries/usePeriods')>();
  return {
    ...actual,
    // CompanyDateInput's internal hook — feed it fixture periods without auth.
    usePeriods: () => ({ data: mockPeriods.current }),
  };
});

import { CompanyDateInput } from '@/components/ui/CompanyDateInput';

function period(p: Partial<FiscalPeriod>): FiscalPeriod {
  return {
    fiscal_year: 2026,
    period: 1,
    period_type: 'NORMAL',
    start_date: '2026-01-01',
    end_date: '2026-01-31',
    status: 'OPEN',
    is_current: false,
    ...p,
  };
}

const JUNE = period({ period: 6, start_date: '2026-06-01', end_date: '2026-06-30' });
const JULY = period({ period: 7, start_date: '2026-07-01', end_date: '2026-07-31', is_current: true });

describe('resolvePeriodLabel (item 5)', () => {
  it('labels an open period', () => {
    const r = resolvePeriodLabel('2026-07-15', [JUNE, JULY]);
    expect(r).toEqual({ text: 'Period 7 (2026-07-01 — 2026-07-31)', isProblem: false });
  });

  it('flags a closed period as a problem', () => {
    const r = resolvePeriodLabel('2026-06-10', [{ ...JUNE, status: 'CLOSED' }, JULY]);
    expect(r?.text).toBe('Period 6 (2026-06-01 — 2026-06-30) ⚠ CLOSED');
    expect(r?.isProblem).toBe(true);
  });

  it('flags an out-of-range date as a problem (backend will refuse)', () => {
    const r = resolvePeriodLabel('2049-01-01', [JUNE, JULY]);
    expect(r).toEqual({ text: 'No fiscal period for this date', isProblem: true });
  });

  it('shows a neutral auto-provision note for an in-range unconfigured year (review C3)', () => {
    // The backend auto-provisions in-range years (A152 item 4) — the warning
    // must not contradict a post that will succeed.
    const lastYear = new Date().getFullYear() - 1;
    const r = resolvePeriodLabel(`${lastYear}-03-10`, [JUNE, JULY]);
    expect(r?.isProblem).toBe(false);
    expect(r?.text).toMatch(/created automatically/);
  });

  it('stays silent on implausible mid-typing years (review C22)', () => {
    // DD/MM/YYYY typing emits '0002-06-15' after the first year digit — no
    // destructive flicker while the year is incomplete.
    expect(resolvePeriodLabel('0002-06-15', [JUNE, JULY])).toBeNull();
  });

  it('returns null with no date or no periods', () => {
    expect(resolvePeriodLabel('', [JUNE])).toBeNull();
    expect(resolvePeriodLabel('2026-07-15', [])).toBeNull();
  });

  it('ignores the ADJUSTMENT period when resolving by date', () => {
    const adj = period({ period: 13, period_type: 'ADJUSTMENT', start_date: '2026-12-31', end_date: '2026-12-31' });
    const dec = period({ period: 12, start_date: '2026-12-01', end_date: '2026-12-31' });
    const r = resolvePeriodLabel('2026-12-31', [adj, dec]);
    expect(r?.text).toContain('Period 12');
  });
});

describe('findPriorOpenPeriod (item 6)', () => {
  it('returns nothing when the prior period is closed (no wallpaper)', () => {
    const r = findPriorOpenPeriod([{ ...JUNE, status: 'CLOSED' }, JULY], new Date(2026, 6, 10));
    expect(r).toBeNull();
  });

  it('is silent during the grace window, then nudges', () => {
    // July 2nd — only 2 days past June's end (< N): silent.
    expect(findPriorOpenPeriod([JUNE, JULY], new Date(2026, 6, 2))).toBeNull();
    // July 4th — 4 days past (≥ N): nudge for June.
    const r = findPriorOpenPeriod([JUNE, JULY], new Date(2026, 6, 4));
    expect(r?.period.period).toBe(6);
    expect(r!.daysPastEnd).toBeGreaterThanOrEqual(PRIOR_PERIOD_NUDGE_DAYS);
  });

  it('picks the MOST RECENTLY ENDED open period when several linger (review C2)', () => {
    // A whole seeded year can be open; the nudge is about the month whose
    // close ritual is due NOW, and it must agree with the Month-End Close
    // page's previous-month default — not pin to last January forever.
    const may = period({ period: 5, start_date: '2026-05-01', end_date: '2026-05-31' });
    const jan = period({ period: 1, start_date: '2026-01-01', end_date: '2026-01-31' });
    const r = findPriorOpenPeriod([jan, JUNE, may, JULY], new Date(2026, 6, 20));
    expect(r?.period.period).toBe(6);
  });

  it('never nudges for the current period itself', () => {
    const r = findPriorOpenPeriod([JULY], new Date(2026, 6, 20));
    expect(r).toBeNull();
  });
});

describe('CompanyDateInput showPeriodWarning (item 5)', () => {
  it('renders no warning by default', () => {
    mockPeriods.current = [{ ...JUNE, status: 'CLOSED' }];
    render(<CompanyDateInput value="2026-06-10" onChange={() => {}} />);
    expect(screen.queryByText(/Period 6/)).not.toBeInTheDocument();
  });

  it('renders the CLOSED warning when opted in', () => {
    mockPeriods.current = [{ ...JUNE, status: 'CLOSED' }];
    render(<CompanyDateInput value="2026-06-10" onChange={() => {}} showPeriodWarning />);
    const warning = screen.getByText(/Period 6 \(2026-06-01 — 2026-06-30\) ⚠ CLOSED/);
    expect(warning).toHaveClass('text-destructive');
  });

  it('renders a neutral label for an open period', () => {
    mockPeriods.current = [JUNE];
    render(<CompanyDateInput value="2026-06-10" onChange={() => {}} showPeriodWarning />);
    const label = screen.getByText(/Period 6 \(2026-06-01 — 2026-06-30\)$/);
    expect(label).toHaveClass('text-muted-foreground');
  });
});
