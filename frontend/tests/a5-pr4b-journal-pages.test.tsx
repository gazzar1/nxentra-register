/**
 * A5-PR4b — new.tsx / edit.tsx page logic (form stubbed).
 *
 * M-5: typed source fields reach create and PATCH.
 * M-6: the save-complete payload never carries them.
 * M-8: unknown query kinds and array-valued params are ignored safely.
 * M-9: valid query params survive router hydration and prefill exactly.
 * M-10: raw source_module/source_document query params are never trusted.
 * M-11: one stable Idempotency-Key is reused across create retries.
 * (+ edit: unchanged source → PATCH omits the fields; system-owned draft →
 *  fields never sent; cleared source → both sent as ''.)
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { routerState, submitArgs, mockCreate, mockSaveComplete, mockUpdate, entryState } =
  vi.hoisted(() => ({
    routerState: {
      isReady: true,
      query: {} as Record<string, string | string[]>,
      push: vi.fn(),
      back: vi.fn(),
    },
    // What the stubbed form passes to onSubmit when its button is clicked.
    submitArgs: {
      current: [
        { date: '2026-08-30', period: 8, memo: 'A perfectly valid reason', memo_ar: '', lines: [] },
        false,
        undefined,
      ] as unknown[],
    },
    mockCreate: vi.fn(),
    mockSaveComplete: vi.fn(),
    mockUpdate: vi.fn(),
    entryState: { current: null as Record<string, unknown> | null },
  }));

vi.mock('next-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback || key.split('.').pop() || key,
  }),
}));
vi.mock('next-i18next/serverSideTranslations', () => ({
  serverSideTranslations: vi.fn().mockResolvedValue({}),
}));
vi.mock('next/router', () => ({
  useRouter: () => routerState,
}));
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock('@/components/layout', () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/ui/toaster', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/api-client', () => ({
  default: {},
  getErrorMessage: (e: unknown) => String(e),
}));
// Stub the form: the page logic (prefill parsing, payload assembly, idempotency)
// is under test — the real form has its own suite.
vi.mock('@/components/forms/JournalEntryForm', () => ({
  JournalEntryForm: (props: {
    initialData?: unknown;
    systemOwnedSource?: unknown;
    onSubmit: (...args: unknown[]) => Promise<void>;
  }) => (
    <div>
      <div data-testid="initial-data">{JSON.stringify(props.initialData ?? null)}</div>
      <div data-testid="system-owned">{JSON.stringify(props.systemOwnedSource ?? null)}</div>
      <button data-testid="stub-submit" onClick={() => void props.onSubmit(...submitArgs.current)}>
        stub-submit
      </button>
    </div>
  ),
}));
vi.mock('@/queries/useJournalEntries', () => ({
  useJournalEntry: () => ({ data: entryState.current, isLoading: false }),
  useCreateJournalEntry: () => ({ mutateAsync: mockCreate, isPending: false }),
  useSaveCompleteJournalEntry: () => ({ mutateAsync: mockSaveComplete, isPending: false }),
  useUpdateJournalEntry: () => ({ mutateAsync: mockUpdate, isPending: false }),
}));

import NewJournalEntryPage from '@/pages/accounting/journal-entries/new';
import EditJournalEntryPage from '@/pages/accounting/journal-entries/[id]/edit';

const draftEntry = (over: Record<string, unknown> = {}) => ({
  id: 5,
  public_id: 'pub-5',
  company: 1,
  entry_number: null,
  date: '2026-08-01',
  period: 8,
  memo: 'A valid adjustment reason',
  memo_ar: '',
  currency: 'EGP',
  exchange_rate: '1.000000',
  kind: 'NORMAL',
  status: 'DRAFT',
  source_module: '',
  source_document: '',
  posted_at: null,
  posted_by: null,
  reversed_at: null,
  reversed_by: null,
  reverses_entry: null,
  created_at: '',
  updated_at: '',
  lines: [],
  total_debit: '100.00',
  total_credit: '100.00',
  is_balanced: true,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  routerState.isReady = true;
  routerState.query = {};
  submitArgs.current = [
    { date: '2026-08-30', period: 8, memo: 'A perfectly valid reason', memo_ar: '', lines: [] },
    false,
    undefined,
  ];
  mockCreate.mockResolvedValue({ data: { id: 42 } });
  mockSaveComplete.mockResolvedValue({ data: { id: 42 } });
  mockUpdate.mockResolvedValue({ data: { id: 5 } });
  entryState.current = draftEntry();
});

describe('A5-PR4b new journal page', () => {
  it('M-9: valid whitelisted query params prefill exactly', () => {
    routerState.query = {
      adjustment_source_kind: 'settlement_event',
      adjustment_source_reference: '0f0e-abc',
    };
    render(<NewJournalEntryPage />);
    expect(JSON.parse(screen.getByTestId('initial-data').textContent!)).toEqual({
      adjustment_source_kind: 'settlement_event',
      adjustment_source_reference: '0f0e-abc',
    });
  });

  it('M-9: the form is not rendered before router hydration (no one-shot-reset race)', () => {
    routerState.isReady = false;
    routerState.query = {};
    render(<NewJournalEntryPage />);
    expect(screen.queryByTestId('stub-submit')).toBeNull();
  });

  it('M-8: unknown kinds, blanks, and array-valued params are ignored safely', () => {
    for (const query of [
      { adjustment_source_kind: 'bogus_kind', adjustment_source_reference: 'r' },
      { adjustment_source_kind: ['settlement_event'], adjustment_source_reference: 'r' },
      { adjustment_source_kind: 'settlement_event', adjustment_source_reference: ['r', 'r2'] },
      { adjustment_source_kind: 'settlement_event', adjustment_source_reference: '   ' },
      { adjustment_source_kind: 'settlement_event' },
      { adjustment_source_reference: 'r' },
    ] as Record<string, string | string[]>[]) {
      routerState.query = query;
      const { unmount } = render(<NewJournalEntryPage />);
      expect(JSON.parse(screen.getByTestId('initial-data').textContent!)).toBeNull();
      unmount();
    }
  });

  it('M-10: raw source_module/source_document query params are never consumed', () => {
    routerState.query = {
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    };
    render(<NewJournalEntryPage />);
    expect(JSON.parse(screen.getByTestId('initial-data').textContent!)).toBeNull();
  });

  it('M-5/M-6: create carries the typed source; save-complete never does', async () => {
    submitArgs.current = [
      {
        date: '2026-08-30',
        period: 8,
        memo: 'Drain the residual left by the rejected row',
        memo_ar: '',
        lines: [{ line_no: 1, account_id: 10, debit: 100, credit: 0 }],
      },
      true, // saveAsDraft → the page also calls save-complete
      { kind: 'import_reject', reference: 'pid-11' },
    ];
    render(<NewJournalEntryPage />);
    fireEvent.click(screen.getByTestId('stub-submit'));

    await waitFor(() => expect(mockSaveComplete).toHaveBeenCalledTimes(1));
    const createArg = mockCreate.mock.calls[0][0];
    expect(createArg.data.adjustment_source_kind).toBe('import_reject');
    expect(createArg.data.adjustment_source_reference).toBe('pid-11');
    expect(typeof createArg.idempotencyKey).toBe('string');
    expect(createArg.idempotencyKey.length).toBeGreaterThan(0);

    const saveCompleteData = mockSaveComplete.mock.calls[0][0].data;
    expect('adjustment_source_kind' in saveCompleteData).toBe(false);
    expect('adjustment_source_reference' in saveCompleteData).toBe(false);
  });

  it('an empty source selection is not sent on create', async () => {
    submitArgs.current = [
      { date: '2026-08-30', memo: 'x', memo_ar: '', lines: [] },
      false,
      { kind: '', reference: '' },
    ];
    render(<NewJournalEntryPage />);
    fireEvent.click(screen.getByTestId('stub-submit'));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    const createArg = mockCreate.mock.calls[0][0];
    expect('adjustment_source_kind' in createArg.data).toBe(false);
    expect('adjustment_source_reference' in createArg.data).toBe(false);
  });

  it('M-11: one stable Idempotency-Key is reused across create retries', async () => {
    mockCreate
      .mockRejectedValueOnce(new Error('network blip'))
      .mockResolvedValueOnce({ data: { id: 42 } });
    render(<NewJournalEntryPage />);

    fireEvent.click(screen.getByTestId('stub-submit'));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('stub-submit'));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(2));

    const key1 = mockCreate.mock.calls[0][0].idempotencyKey;
    const key2 = mockCreate.mock.calls[1][0].idempotencyKey;
    expect(key1).toBe(key2);
    expect(typeof key1).toBe('string');
  });
});

describe('A5-PR4b edit journal page', () => {
  it('prefills the parsed typed source for a pilot-adjustment draft', () => {
    entryState.current = draftEntry({
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    });
    render(<EditJournalEntryPage />);
    const initial = JSON.parse(screen.getByTestId('initial-data').textContent!);
    expect(initial.adjustment_source_kind).toBe('settlement_event');
    expect(initial.adjustment_source_reference).toBe('0f0e-abc');
    expect(JSON.parse(screen.getByTestId('system-owned').textContent!)).toBeNull();
  });

  it('M-5: a changed source rides the PATCH; save-complete stays clean (M-6)', async () => {
    entryState.current = draftEntry({
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    });
    submitArgs.current = [
      { date: '2026-08-01', period: 8, memo: 'Corrected to the bank line', memo_ar: '', lines: [] },
      true,
      { kind: 'bank_line', reference: 'bl-9' },
    ];
    render(<EditJournalEntryPage />);
    fireEvent.click(screen.getByTestId('stub-submit'));

    await waitFor(() => expect(mockSaveComplete).toHaveBeenCalledTimes(1));
    const patched = mockUpdate.mock.calls[0][0].data;
    expect(patched.adjustment_source_kind).toBe('bank_line');
    expect(patched.adjustment_source_reference).toBe('bl-9');
    const saveCompleteData = mockSaveComplete.mock.calls[0][0].data;
    expect('adjustment_source_kind' in saveCompleteData).toBe(false);
    expect('adjustment_source_reference' in saveCompleteData).toBe(false);
  });

  it('an unchanged source is omitted from the PATCH body', async () => {
    entryState.current = draftEntry({
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    });
    submitArgs.current = [
      { date: '2026-08-01', memo: 'Only the memo changed here', memo_ar: '', lines: [] },
      false,
      { kind: 'settlement_event', reference: '0f0e-abc' },
    ];
    render(<EditJournalEntryPage />);
    fireEvent.click(screen.getByTestId('stub-submit'));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const patched = mockUpdate.mock.calls[0][0].data;
    expect('adjustment_source_kind' in patched).toBe(false);
    expect('adjustment_source_reference' in patched).toBe(false);
  });

  it('clearing the source sends the explicit both-blank pair', async () => {
    entryState.current = draftEntry({
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    });
    submitArgs.current = [
      { date: '2026-08-01', memo: 'Back to a plain draft', memo_ar: '', lines: [] },
      false,
      { kind: '', reference: '' },
    ];
    render(<EditJournalEntryPage />);
    fireEvent.click(screen.getByTestId('stub-submit'));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const patched = mockUpdate.mock.calls[0][0].data;
    expect(patched.adjustment_source_kind).toBe('');
    expect(patched.adjustment_source_reference).toBe('');
  });

  it('M-7: a system-owned draft passes read-only provenance and never sends the fields', async () => {
    entryState.current = draftEntry({
      source_module: 'platform_stripe',
      source_document: 'po_pr4b_sys',
    });
    // Even if a stale form somehow handed back a selection, the page must drop it.
    submitArgs.current = [
      { date: '2026-08-01', memo: 'Attempted relabel', memo_ar: '', lines: [] },
      false,
      { kind: 'bank_line', reference: 'bl-9' },
    ];
    render(<EditJournalEntryPage />);
    expect(JSON.parse(screen.getByTestId('system-owned').textContent!)).toEqual({
      module: 'platform_stripe',
      document: 'po_pr4b_sys',
    });
    fireEvent.click(screen.getByTestId('stub-submit'));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const patched = mockUpdate.mock.calls[0][0].data;
    expect('adjustment_source_kind' in patched).toBe(false);
    expect('adjustment_source_reference' in patched).toBe(false);
  });
});
