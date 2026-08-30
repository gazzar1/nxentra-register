/**
 * A5-PR4b — journal-entry detail page: post readiness, traceability card,
 * and the reversal dialog.
 *
 * M-12: active-pilot untraced DRAFT renders a persistent not-ready state.
 * M-13: a traced DRAFT exposes the Post action.
 * M-14: a system-owned DRAFT does not invite manual relabelling.
 * M-15: a posted pilot adjustment displays reason, kind, and canonical reference.
 * M-16: unknown/malformed stored provenance renders safely.
 * M-17: system provenance is never mislabeled as a pilot adjustment.
 * M-18/M-19: profile NONE keeps the old confirmation and empty-body reverse.
 * M-20: a pilot-adjustment reversal requires a reason and inherits the source.
 * M-21: a blank-provenance pilot reversal requires reason plus a new source.
 * M-23: a failed reversal leaves the dialog and input intact.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

const { authState, entryState, mockPost, mockReverse, mockDelete } = vi.hoisted(() => ({
  authState: { company: {} as Record<string, unknown> },
  entryState: { current: null as Record<string, unknown> | null },
  mockPost: vi.fn(),
  mockReverse: vi.fn(),
  mockDelete: vi.fn(),
}));

vi.mock('next-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback || key.split('.').pop() || key,
  }),
}));
vi.mock('next-i18next/serverSideTranslations', () => ({
  serverSideTranslations: vi.fn().mockResolvedValue({}),
}));
const mockRouterPush = vi.fn();
vi.mock('next/router', () => ({
  useRouter: () => ({ push: mockRouterPush, query: { id: '5' }, locale: 'en' }),
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
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ company: authState.company }),
}));
vi.mock('@/hooks/useCompanyFormat', () => ({
  useCompanyFormat: () => ({
    formatCurrency: (v: unknown) => String(v),
    formatAmount: (v: unknown) => String(v),
    formatDate: (v: unknown) => String(v),
  }),
}));
vi.mock('@/lib/api-client', () => ({
  default: {},
  getErrorMessage: (e: unknown) => (e instanceof Error ? e.message : String(e)),
}));
vi.mock('@/queries/useJournalEntries', () => ({
  useJournalEntry: () => ({ data: entryState.current, isLoading: false }),
  usePostJournalEntry: () => ({ mutateAsync: mockPost, isPending: false }),
  useReverseJournalEntry: () => ({ mutateAsync: mockReverse, isPending: false }),
  useDeleteJournalEntry: () => ({ mutateAsync: mockDelete, isPending: false }),
}));

import JournalEntryDetailPage from '@/pages/accounting/journal-entries/[id]';

const entry = (over: Record<string, unknown> = {}) => ({
  id: 5,
  public_id: 'pub-5',
  company: 1,
  entry_number: 'JE-000005',
  date: '2026-08-01',
  period: 8,
  memo: 'A valid adjustment reason text',
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
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
  lines: [],
  total_debit: '100.00',
  total_credit: '100.00',
  is_balanced: true,
  ...over,
});

const setPilot = (on: boolean) => {
  authState.company = {
    id: 1,
    name: 'Pilot Co',
    default_currency: 'EGP',
    functional_currency: 'EGP',
    pilot_profile: on ? 'ISOLATED_SHADOW_LEDGER_V1' : 'NONE',
  };
};

beforeEach(() => {
  vi.clearAllMocks();
  setPilot(true);
  mockReverse.mockResolvedValue({ data: { id: 9 } });
});

describe('A5-PR4b detail page — post readiness', () => {
  it('M-12: an untraced active-pilot DRAFT shows a persistent not-ready state, no Post', () => {
    entryState.current = entry(); // blank source, DRAFT, balanced
    render(<JournalEntryDetailPage />);

    expect(screen.getByText(/Not ready to post/)).toBeInTheDocument();
    expect(screen.getByText(/server makes the final decision/)).toBeInTheDocument();
    expect(screen.getByText('Edit adjustment evidence')).toBeInTheDocument();
    expect(screen.queryByText('postEntry')).toBeNull();
  });

  it('M-13: a locally traced DRAFT exposes the Post action', () => {
    entryState.current = entry({
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    });
    render(<JournalEntryDetailPage />);

    expect(screen.queryByText(/Not ready to post/)).toBeNull();
    expect(screen.getByText('postEntry')).toBeInTheDocument();
  });

  it('a traced DRAFT with an out-of-bounds reason is still not ready', () => {
    entryState.current = entry({
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
      memo: 'too short',
    });
    render(<JournalEntryDetailPage />);
    expect(screen.getByText(/Not ready to post/)).toBeInTheDocument();
    expect(screen.queryByText('postEntry')).toBeNull();
  });

  it('M-14: a system-owned DRAFT is not invited to relabel or post', () => {
    entryState.current = entry({
      source_module: 'platform_stripe',
      source_document: 'po_pr4b_sys',
    });
    render(<JournalEntryDetailPage />);

    expect(
      screen.getByText(/belongs to an automated process \(system-owned provenance\)/)
    ).toBeInTheDocument();
    expect(screen.queryByText('Edit adjustment evidence')).toBeNull();
    expect(screen.queryByText('postEntry')).toBeNull();
  });

  it('M-18: profile NONE keeps the plain Post flow with no pilot banners', () => {
    setPilot(false);
    entryState.current = entry();
    render(<JournalEntryDetailPage />);

    expect(screen.queryByText(/Not ready to post/)).toBeNull();
    expect(screen.getByText('postEntry')).toBeInTheDocument();
    expect(screen.queryByText('Adjustment traceability')).toBeNull();
    expect(screen.queryByText('System provenance')).toBeNull();
  });
});

describe('A5-PR4b detail page — traceability card', () => {
  it('M-15: a posted pilot adjustment shows reason, kind label, and monospace reference', () => {
    entryState.current = entry({
      status: 'POSTED',
      posted_at: '2026-08-02T00:00:00Z',
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    });
    render(<JournalEntryDetailPage />);

    expect(screen.getByText('Pilot adjustment')).toBeInTheDocument();
    expect(screen.getByText('Adjustment traceability')).toBeInTheDocument();
    // The memo also renders as the page subtitle and the entry-info memo —
    // scope to the traceability card's reason block.
    const reasonBlock = screen.getByText('Adjustment reason').parentElement!;
    expect(within(reasonBlock).getByText('A valid adjustment reason text')).toBeInTheDocument();
    expect(screen.getByText('Settlement event')).toBeInTheDocument();
    const ref = screen.getByText('settlement_event:0f0e-abc');
    expect(ref.tagName).toBe('CODE');
    expect(screen.getByTitle('Copy reference')).toBeInTheDocument();
    // Area-level navigation with the honest label.
    const area = screen.getByText(/Open related area/);
    expect(area.closest('a')!.getAttribute('href')).toBe('/finance/reconciliation#stage-2');
  });

  it('a reversal entry labels the reason as the reversal narrative', () => {
    entryState.current = entry({
      status: 'POSTED',
      kind: 'REVERSAL',
      reverses_entry: 4,
      reverses_entry_number: 'JE-000004',
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
      memo: 'Wrong batch — Reverses JE-000004',
    });
    render(<JournalEntryDetailPage />);
    expect(screen.getByText('Reversal narrative')).toBeInTheDocument();
  });

  it('M-16: unknown or malformed stored provenance renders safely as raw text', () => {
    entryState.current = entry({
      status: 'POSTED',
      source_module: 'pilot_adjustment',
      source_document: 'garbage-without-a-known-kind',
    });
    render(<JournalEntryDetailPage />);

    expect(screen.getByText('Pilot adjustment')).toBeInTheDocument();
    expect(screen.getByText('Source reference (raw)')).toBeInTheDocument();
    expect(screen.getByText('garbage-without-a-known-kind')).toBeInTheDocument();
    expect(screen.queryByText(/Open related area/)).toBeNull();
  });

  it('M-17: system provenance renders as System provenance, never as a pilot adjustment', () => {
    entryState.current = entry({
      status: 'POSTED',
      source_module: 'platform_stripe',
      source_document: 'po_pr4b_sys',
    });
    render(<JournalEntryDetailPage />);

    expect(screen.getByText('System provenance')).toBeInTheDocument();
    expect(screen.getByText('platform_stripe')).toBeInTheDocument();
    expect(screen.queryByText('Pilot adjustment')).toBeNull();
    expect(screen.queryByText('Adjustment traceability')).toBeNull();
  });
});

describe('A5-PR4b detail page — reversal', () => {
  it('M-19: profile NONE keeps the simple confirmation and the empty-body reverse', async () => {
    setPilot(false);
    entryState.current = entry({ status: 'POSTED', posted_at: '2026-08-02T00:00:00Z' });
    render(<JournalEntryDetailPage />);

    fireEvent.click(screen.getByText('reverseEntry'));
    // The old ConfirmDialog copy — not the pilot input dialog.
    expect(screen.getByText('reverseConfirm')).toBeInTheDocument();
    expect(screen.queryByText('Reversal reason')).toBeNull();

    fireEvent.click(screen.getByText('confirm'));
    await waitFor(() => expect(mockReverse).toHaveBeenCalledTimes(1));
    expect(mockReverse).toHaveBeenCalledWith({ id: 5 });
  });

  it('M-20: a pilot-adjustment reversal shows the inherited source and sends only the reason', async () => {
    entryState.current = entry({
      status: 'POSTED',
      posted_at: '2026-08-02T00:00:00Z',
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    });
    render(<JournalEntryDetailPage />);

    fireEvent.click(screen.getAllByText('reverseEntry')[0]);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/Inherited source \(read-only\)/)).toBeInTheDocument();
    expect(within(dialog).getByText('settlement_event:0f0e-abc')).toBeInTheDocument();
    // The reason input starts EMPTY — the original memo is never copied in.
    const reason = within(dialog).getByLabelText('Reversal reason') as HTMLTextAreaElement;
    expect(reason.value).toBe('');
    expect(
      within(dialog).getByText(/does not resolve or repair the referenced source item/)
    ).toBeInTheDocument();

    // A too-short reason refuses client-side.
    fireEvent.change(reason, { target: { value: 'short' } });
    fireEvent.click(within(dialog).getAllByText('reverseEntry').pop()!);
    expect(
      await within(dialog).findByText(/reversal reason must be 10–180 characters/)
    ).toBeInTheDocument();
    expect(mockReverse).not.toHaveBeenCalled();

    fireEvent.change(reason, { target: { value: 'Posted against the wrong batch' } });
    fireEvent.click(within(dialog).getAllByText('reverseEntry').pop()!);
    await waitFor(() => expect(mockReverse).toHaveBeenCalledTimes(1));
    expect(mockReverse).toHaveBeenCalledWith({
      id: 5,
      payload: { reason: 'Posted against the wrong batch' },
    });
  });

  it('M-21: a blank-provenance reversal requires a new source in addition to the reason', async () => {
    entryState.current = entry({ status: 'POSTED', posted_at: '2026-08-02T00:00:00Z' });
    render(<JournalEntryDetailPage />);

    fireEvent.click(screen.getAllByText('reverseEntry')[0]);
    const dialog = screen.getByRole('dialog');
    expect(
      within(dialog).getByText(/the reversal becomes\s+a new supervised pilot adjustment/)
    ).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText('Reversal reason'), {
      target: { value: 'A perfectly valid reversal reason' },
    });
    fireEvent.click(within(dialog).getAllByText('reverseEntry').pop()!);
    expect(
      await within(dialog).findByText('Choose a source type and reference for the reversal.')
    ).toBeInTheDocument();
    expect(mockReverse).not.toHaveBeenCalled();
  });

  it('M-23: a failed reversal keeps the dialog open with the input intact', async () => {
    mockReverse.mockRejectedValueOnce(new Error('source belongs to another company'));
    entryState.current = entry({
      status: 'POSTED',
      posted_at: '2026-08-02T00:00:00Z',
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    });
    render(<JournalEntryDetailPage />);

    fireEvent.click(screen.getAllByText('reverseEntry')[0]);
    const dialog = screen.getByRole('dialog');
    const reason = within(dialog).getByLabelText('Reversal reason') as HTMLTextAreaElement;
    fireEvent.change(reason, { target: { value: 'Posted against the wrong batch' } });
    fireEvent.click(within(dialog).getAllByText('reverseEntry').pop()!);

    await waitFor(() => expect(mockReverse).toHaveBeenCalledTimes(1));
    // Dialog still open, error surfaced, operator input preserved.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(
      within(screen.getByRole('dialog')).getByText('source belongs to another company')
    ).toBeInTheDocument();
    expect(
      (within(screen.getByRole('dialog')).getByLabelText('Reversal reason') as HTMLTextAreaElement)
        .value
    ).toBe('Posted against the wrong batch');
  });

  it('cancel clears the transient reversal fields', async () => {
    entryState.current = entry({
      status: 'POSTED',
      posted_at: '2026-08-02T00:00:00Z',
      source_module: 'pilot_adjustment',
      source_document: 'settlement_event:0f0e-abc',
    });
    render(<JournalEntryDetailPage />);

    fireEvent.click(screen.getAllByText('reverseEntry')[0]);
    let dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Reversal reason'), {
      target: { value: 'Some abandoned draft reason' },
    });
    fireEvent.click(within(dialog).getByText('cancel'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getAllByText('reverseEntry')[0]);
    dialog = screen.getByRole('dialog');
    expect((within(dialog).getByLabelText('Reversal reason') as HTMLTextAreaElement).value).toBe('');
  });
});
