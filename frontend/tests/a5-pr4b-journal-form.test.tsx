/**
 * A5-PR4b — JournalEntryForm pilot-adjustment behavior.
 *
 * M-1: active-pilot form shows the evidence section + Adjustment reason label.
 * M-2: profile NONE preserves the current UI and payload (no source arg).
 * M-3: source kind/reference are both-or-neither at the client.
 * M-4: INCOMPLETE/DRAFT save stays possible without a trace.
 * M-7: system-owned draft provenance renders read-only and is never emitted.
 * (+ hydration: a prefilled/initial source survives the one-shot reset — M-9's
 *  form half — and flows to onSubmit — M-5's form half.)
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { authState, EMPTY_LIST } = vi.hoisted(() => ({
  authState: {
    company: {
      id: 1,
      name: 'Pilot Co',
      default_currency: 'EGP',
      functional_currency: 'EGP',
      pilot_profile: 'ISOLATED_SHADOW_LEDGER_V1',
    } as Record<string, unknown>,
  },
  // Stable identity — the form's account/dimension effects key on reference
  // equality (react-query data is referentially stable in production; a fresh
  // [] per render would spin the init effect forever).
  EMPTY_LIST: [] as never[],
}));

vi.mock('next-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback || key.split('.').pop() || key,
  }),
}));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ company: authState.company }),
}));
vi.mock('@/queries/useAccounts', () => ({
  useAccounts: () => ({ data: EMPTY_LIST }),
  useDimensions: () => ({ data: EMPTY_LIST }),
}));
vi.mock('@/services/accounts.service', () => ({
  accountsService: { getAnalysisDefaults: vi.fn().mockResolvedValue({ data: [] }) },
}));
vi.mock('@/services/exchange-rates.service', () => ({
  exchangeRatesService: { lookup: vi.fn().mockResolvedValue({ data: { rate: null } }) },
}));
vi.mock('@/services/periods.service', () => ({
  periodsService: { list: vi.fn().mockResolvedValue({ data: { periods: [] } }) },
}));
vi.mock('@/hooks/useArabicFields', () => ({ useArabicFields: () => false }));
vi.mock('@/components/forms/ArabicField', () => ({
  ArabicField: () => null,
}));
vi.mock('@/components/common/BilingualText', () => ({
  useBilingualText: () => (en: string) => en,
}));
vi.mock('@/components/ui/CompanyDateInput', () => ({
  CompanyDateInput: ({ value, onChange, id }: { value: string; onChange: (v: string) => void; id?: string }) => (
    <input id={id} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));
vi.mock('@/components/ui/FormattedAmountInput', () => ({
  FormattedAmountInput: ({ value, onChange }: { value: number; onChange: (v: number) => void }) => (
    <input value={String(value)} onChange={(e) => onChange(Number(e.target.value) || 0)} />
  ),
}));
vi.mock('@/lib/useFormKeyboardShortcuts', () => ({ useFormKeyboardShortcuts: () => undefined }));

import { JournalEntryForm } from '@/components/forms/JournalEntryForm';

const validLines = [
  { account_id: 10, debit: 100, credit: 0, description: '', description_ar: '', analysis_tags: [] },
  { account_id: 20, debit: 0, credit: 100, description: '', description_ar: '', analysis_tags: [] },
];

const setPilot = (on: boolean) => {
  authState.company = {
    id: 1,
    name: 'Pilot Co',
    default_currency: 'EGP',
    functional_currency: 'EGP',
    ...(on ? { pilot_profile: 'ISOLATED_SHADOW_LEDGER_V1' } : { pilot_profile: 'NONE' }),
  };
};

beforeEach(() => {
  vi.clearAllMocks();
  setPilot(true);
});

describe('A5-PR4b JournalEntryForm', () => {
  it('M-1: active pilot shows the evidence section and Adjustment reason', () => {
    render(<JournalEntryForm onSubmit={vi.fn()} />);
    expect(screen.getByText('Pilot adjustment evidence')).toBeInTheDocument();
    expect(
      screen.getByText(
        /Required before posting\. Saving an incomplete or draft entry does not resolve the source item\./
      )
    ).toBeInTheDocument();
    expect(screen.getByText('Adjustment reason')).toBeInTheDocument();
    expect(screen.getByText(/Required before posting · 10–180 characters/)).toBeInTheDocument();
    expect(screen.getByText('Source type')).toBeInTheDocument();
    expect(screen.getByLabelText('Source reference')).toBeInTheDocument();
  });

  it('M-2: profile NONE hides the section, keeps memo semantics, sends no source', async () => {
    setPilot(false);
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<JournalEntryForm onSubmit={onSubmit} initialData={{ lines: validLines }} />);

    expect(screen.queryByText('Pilot adjustment evidence')).toBeNull();
    expect(screen.queryByText('Adjustment reason')).toBeNull();
    // The plain memo label is back (t-key fallback renders the key tail).
    expect(screen.getByText('memo')).toBeInTheDocument();

    fireEvent.click(screen.getByText(/\(Incomplete\)/));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const [payload, saveAsDraft, source] = onSubmit.mock.calls[0];
    expect(saveAsDraft).toBe(false);
    expect(source).toBeUndefined();
    expect('adjustment_source_kind' in payload).toBe(false);
    expect('adjustment_source_reference' in payload).toBe(false);
  });

  it('M-3: a reference without a source type refuses client-side', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<JournalEntryForm onSubmit={onSubmit} initialData={{ lines: validLines }} />);

    fireEvent.change(screen.getByLabelText('Source reference'), {
      target: { value: 'dangling-reference' },
    });
    fireEvent.click(screen.getByText(/\(Incomplete\)/));

    await waitFor(() =>
      expect(
        screen.getByText('Provide both a source type and a reference, or leave both blank.')
      ).toBeInTheDocument()
    );
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('M-4: an incomplete save without any trace still submits (empty source)', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<JournalEntryForm onSubmit={onSubmit} initialData={{ lines: validLines }} />);

    fireEvent.click(screen.getByText(/\(Incomplete\)/));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const [, , source] = onSubmit.mock.calls[0];
    expect(source).toEqual({ kind: '', reference: '' });
  });

  it('form half of M-5/M-9: a prefilled source survives hydration and reaches onSubmit', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <JournalEntryForm
        onSubmit={onSubmit}
        initialData={{
          lines: validLines,
          adjustment_source_kind: 'settlement_event',
          adjustment_source_reference: '0f0e-abc',
        }}
      />
    );

    // The initial/prefilled reference is preserved (hydration goes through
    // form.reset, never the kind-change clear).
    await waitFor(() =>
      expect((screen.getByLabelText('Source reference') as HTMLInputElement).value).toBe('0f0e-abc')
    );
    // The per-kind hint renders for the hydrated kind.
    expect(screen.getByText(/BusinessEvent UUID of the settlement event\./)).toBeInTheDocument();

    fireEvent.click(screen.getByText(/\(Incomplete\)/));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const [, , source] = onSubmit.mock.calls[0];
    expect(source).toEqual({ kind: 'settlement_event', reference: '0f0e-abc' });
  });

  it('Clear source returns the pair to blank and re-enables an untraced save', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <JournalEntryForm
        onSubmit={onSubmit}
        initialData={{
          lines: validLines,
          adjustment_source_kind: 'settlement_event',
          adjustment_source_reference: '0f0e-abc',
        }}
      />
    );

    await waitFor(() =>
      expect((screen.getByLabelText('Source reference') as HTMLInputElement).value).toBe('0f0e-abc')
    );
    fireEvent.click(screen.getByText('Clear source'));
    expect((screen.getByLabelText('Source reference') as HTMLInputElement).value).toBe('');

    fireEvent.click(screen.getByText(/\(Incomplete\)/));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const [, , source] = onSubmit.mock.calls[0];
    // The explicit both-blank pair — the PATCH clear path is reachable.
    expect(source).toEqual({ kind: '', reference: '' });
  });

  it('M-7: a system-owned draft shows read-only provenance and emits no source', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <JournalEntryForm
        onSubmit={onSubmit}
        initialData={{ lines: validLines }}
        systemOwnedSource={{ module: 'platform_stripe', document: 'po_pr4b_sys' }}
      />
    );

    expect(screen.getByText('System provenance')).toBeInTheDocument();
    expect(screen.getByText('platform_stripe')).toBeInTheDocument();
    expect(screen.getByText('po_pr4b_sys')).toBeInTheDocument();
    expect(
      screen.getByText(/system-owned and cannot be relabelled through the manual form/)
    ).toBeInTheDocument();
    // The editable evidence section is suppressed.
    expect(screen.queryByText('Pilot adjustment evidence')).toBeNull();
    expect(screen.queryByLabelText('Source reference')).toBeNull();
    // The memo keeps its plain meaning — a system-owned draft is not invited to
    // author an "adjustment reason" it can never post through the manual form.
    expect(screen.queryByText('Adjustment reason')).toBeNull();
    expect(screen.getByText('memo')).toBeInTheDocument();

    fireEvent.click(screen.getByText(/\(Incomplete\)/));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const [, , source] = onSubmit.mock.calls[0];
    // No source selection at all — the page must not send blank fields that
    // would try to clear the system stamp.
    expect(source).toBeUndefined();
  });
});
