/**
 * A5-PR4b — pilot-adjustment display vocabulary + journal service contract.
 *
 * The lib module is a DISPLAY convenience only (the backend registry owns
 * validity); these tests pin its parse/build behavior and that the service
 * sends the EXACT backend field/header names:
 *   - create: optional `Idempotency-Key` header; typed adjustment fields ride
 *     the payload verbatim (M-11 partial, M-29: one create endpoint).
 *   - reverse: optional body with exactly reason / adjustment_source_kind /
 *     adjustment_source_reference (M-22); no payload → no body (profile NONE).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

import {
  PILOT_ADJUSTMENT_SOURCE_KINDS,
  PILOT_ADJUSTMENT_SOURCE_MODULE,
  isPilotAdjustmentSourceKind,
  pilotAdjustmentKindLabel,
  pilotAdjustmentReferenceHint,
  parseSourceDocument,
  relatedAreaFor,
  buildNewAdjustmentHref,
} from '@/lib/pilot-adjustments';

vi.mock('@/lib/api-client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  getErrorMessage: vi.fn(),
}));

import apiClient from '@/lib/api-client';
import { journalService } from '@/services/journal.service';

const client = vi.mocked(apiClient);

describe('A5-PR4b display vocabulary (lib/pilot-adjustments)', () => {
  it('knows exactly the seven supported kinds', () => {
    expect(PILOT_ADJUSTMENT_SOURCE_KINDS).toEqual([
      'projection_failure',
      'import_reject',
      'shopify_reject',
      'shopify_order',
      'shopify_refund',
      'settlement_event',
      'bank_line',
    ]);
    for (const k of PILOT_ADJUSTMENT_SOURCE_KINDS) {
      expect(isPilotAdjustmentSourceKind(k)).toBe(true);
      expect(pilotAdjustmentKindLabel(k)).not.toBe(k); // every kind has a label
      expect(pilotAdjustmentReferenceHint(k).length).toBeGreaterThan(0);
    }
    expect(isPilotAdjustmentSourceKind('bogus')).toBe(false);
    // Object-prototype names must not read as kinds (own-property guard).
    expect(isPilotAdjustmentSourceKind('toString')).toBe(false);
    expect(isPilotAdjustmentSourceKind('__proto__')).toBe(false);
    expect(isPilotAdjustmentSourceKind(null)).toBe(false);
    expect(isPilotAdjustmentSourceKind(7)).toBe(false);
  });

  it('parses canonical "<kind>:<body>" and refuses everything else', () => {
    expect(parseSourceDocument('settlement_event:0f0e-abc')).toEqual({
      kind: 'settlement_event',
      body: '0f0e-abc',
    });
    // A body containing colons keeps everything after the FIRST separator.
    expect(parseSourceDocument('bank_line:a:b')).toEqual({ kind: 'bank_line', body: 'a:b' });
    expect(parseSourceDocument('unknown_kind:x')).toBeNull();
    expect(parseSourceDocument('settlement_event:')).toBeNull();
    expect(parseSourceDocument(':body')).toBeNull();
    expect(parseSourceDocument('no-separator')).toBeNull();
    expect(parseSourceDocument('')).toBeNull();
    expect(parseSourceDocument(null)).toBeNull();
    expect(parseSourceDocument(undefined)).toBeNull();
  });

  it('unknown stored kinds render as raw provenance, never crash', () => {
    expect(pilotAdjustmentKindLabel('mystery_kind')).toBe('mystery_kind');
    expect(pilotAdjustmentReferenceHint('mystery_kind')).toBe('');
    expect(relatedAreaFor('mystery_kind')).toBeNull();
  });

  it('related areas are area-level navigation with an honest label', () => {
    expect(relatedAreaFor('projection_failure')).toEqual({
      href: '/finance/exceptions',
      label: 'Open related area',
    });
    expect(relatedAreaFor('import_reject')!.href).toBe('/finance/exceptions');
    expect(relatedAreaFor('shopify_reject')!.href).toBe('/finance/exceptions');
    expect(relatedAreaFor('shopify_order')!.href).toBe('/shopify/orders');
    expect(relatedAreaFor('shopify_refund')!.href).toBe('/finance/reconciliation');
    expect(relatedAreaFor('settlement_event')!.href).toBe('/finance/reconciliation#stage-2');
    expect(relatedAreaFor('bank_line')!.href).toBe('/accounting/bank-reconciliation');
    // Never a claim of exact-row focus.
    for (const k of PILOT_ADJUSTMENT_SOURCE_KINDS) {
      expect(relatedAreaFor(k)!.label).toBe('Open related area');
    }
  });

  it('builds the new-adjustment URL from the two whitelisted params only', () => {
    const href = buildNewAdjustmentHref('import_reject', 'pid-11');
    expect(href).toBe(
      '/accounting/journal-entries/new?adjustment_source_kind=import_reject&adjustment_source_reference=pid-11'
    );
    // URL-encodes reference bodies; never emits raw source_module/source_document.
    const enc = buildNewAdjustmentHref('bank_line', 'a b&c');
    expect(enc).toContain('adjustment_source_reference=a+b%26c');
    expect(enc).not.toContain('source_module');
    expect(enc).not.toContain('source_document');
    expect(PILOT_ADJUSTMENT_SOURCE_MODULE).toBe('pilot_adjustment');
  });
});

describe('A5-PR4b journal service contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('create without a key keeps the original 2-arg call (no headers config)', async () => {
    const payload = { date: '2026-08-30', lines: [] };
    await journalService.create(payload as never);
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/', payload);
    expect(client.post).toHaveBeenCalledTimes(1);
    expect((client.post as ReturnType<typeof vi.fn>).mock.calls[0]).toHaveLength(2);
  });

  it('create with a key sends the exact Idempotency-Key header (M-11)', async () => {
    const payload = {
      date: '2026-08-30',
      memo: 'Drain the stale EBD residual',
      adjustment_source_kind: 'settlement_event',
      adjustment_source_reference: '0f0e-abc',
      lines: [],
    };
    await journalService.create(payload as never, 'key-123');
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/', payload, {
      headers: { 'Idempotency-Key': 'key-123' },
    });
    // The typed fields ride the payload under their exact backend names.
    const sent = (client.post as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(sent.adjustment_source_kind).toBe('settlement_event');
    expect(sent.adjustment_source_reference).toBe('0f0e-abc');
    expect('source_module' in sent).toBe(false);
    expect('source_document' in sent).toBe(false);
  });

  it('reverse without a payload sends no body (profile NONE — M-19 wire shape)', async () => {
    await journalService.reverse(5);
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/5/reverse/');
    expect((client.post as ReturnType<typeof vi.fn>).mock.calls[0]).toHaveLength(1);
  });

  it('reverse with a payload sends the exact backend body names (M-22)', async () => {
    await journalService.reverse(5, {
      reason: 'Posted against the wrong settlement batch',
      adjustment_source_kind: 'bank_line',
      adjustment_source_reference: 'bl-9',
    });
    expect(client.post).toHaveBeenCalledWith('/accounting/journal-entries/5/reverse/', {
      reason: 'Posted against the wrong settlement batch',
      adjustment_source_kind: 'bank_line',
      adjustment_source_reference: 'bl-9',
    });
  });

  it('reverse with only a reason omits the source fields (inherited provenance)', async () => {
    await journalService.reverse(5, { reason: 'Duplicate of JE-000004 adjustment' });
    const sent = (client.post as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(sent).toEqual({ reason: 'Duplicate of JE-000004 adjustment' });
  });

  it('M-29: there is exactly one create endpoint — prefilled and direct entry share it', async () => {
    await journalService.create({ date: 'a', lines: [] } as never);
    await journalService.create(
      { date: 'b', adjustment_source_kind: 'import_reject', adjustment_source_reference: 'p', lines: [] } as never,
      'k'
    );
    const urls = (client.post as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(urls).toEqual(['/accounting/journal-entries/', '/accounting/journal-entries/']);
  });
});
