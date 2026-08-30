/**
 * A5-PR4b — the REAL useJournalEntries mutation glue.
 *
 * The page suites mock the hooks module and the service suite mocks the axios
 * client, so without this file no test executes the hook->service seam that
 * decides header-forwarding and body-vs-no-body (proven by mutation testing in
 * the pre-push review). These tests render the real hooks over a mocked
 * journalService and pin the exact forwarding.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { svc } = vi.hoisted(() => ({
  svc: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    saveComplete: vi.fn(),
    post: vi.fn(),
    reverse: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('@/services/journal.service', () => ({ journalService: svc }));

import {
  useCreateJournalEntry,
  useReverseJournalEntry,
} from '@/queries/useJournalEntries';

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  svc.create.mockResolvedValue({ data: { id: 42 } });
  svc.reverse.mockResolvedValue({ data: { id: 9 } });
});

describe('A5-PR4b real hook glue', () => {
  it('useCreateJournalEntry forwards the payload AND the idempotency key', async () => {
    const { result } = renderHook(() => useCreateJournalEntry(), { wrapper });
    const data = { date: '2026-08-30', lines: [] } as never;

    await result.current.mutateAsync({ data, idempotencyKey: 'key-abc' });
    await waitFor(() => expect(svc.create).toHaveBeenCalledTimes(1));
    expect(svc.create).toHaveBeenCalledWith(data, 'key-abc');
  });

  it('useCreateJournalEntry with no key forwards undefined (old wire shape)', async () => {
    const { result } = renderHook(() => useCreateJournalEntry(), { wrapper });
    const data = { date: '2026-08-30', lines: [] } as never;

    await result.current.mutateAsync({ data });
    expect(svc.create).toHaveBeenCalledWith(data, undefined);
  });

  it('useReverseJournalEntry forwards the payload when given', async () => {
    const { result } = renderHook(() => useReverseJournalEntry(), { wrapper });
    const payload = {
      reason: 'Posted against the wrong settlement batch',
      adjustment_source_kind: 'bank_line' as const,
      adjustment_source_reference: 'bl-9',
    };

    await result.current.mutateAsync({ id: 5, payload });
    expect(svc.reverse).toHaveBeenCalledWith(5, payload);
  });

  it('useReverseJournalEntry with no payload forwards undefined (profile NONE)', async () => {
    const { result } = renderHook(() => useReverseJournalEntry(), { wrapper });

    await result.current.mutateAsync({ id: 5 });
    expect(svc.reverse).toHaveBeenCalledWith(5, undefined);
  });
});
