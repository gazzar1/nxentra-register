/**
 * Sentry scrub helpers (S1 hardening) — prove the connect-form credential can't
 * leak into client error telemetry (events, breadcrumbs).
 */
import { describe, it, expect } from 'vitest';
import {
  REDACTED,
  scrubSecrets,
  scrubSentryEvent,
  scrubBreadcrumb,
} from '@/lib/sentry-scrub';

describe('scrubSecrets', () => {
  it('redacts secret-looking string values (rk_/sk_)', () => {
    expect(scrubSecrets('key is rk_live_abcd1234')).toBe(`key is ${REDACTED}`);
    expect(scrubSecrets('sk_test_zzzz9999 leaked')).toBe(`${REDACTED} leaked`);
  });

  it('redacts fields whose NAME is credential-like, regardless of value', () => {
    const out = scrubSecrets({
      credential: 'whatever',
      apiKey: 'x',
      note: 'ok',
    }) as Record<string, unknown>;
    expect(out.credential).toBe(REDACTED);
    expect(out.apiKey).toBe(REDACTED);
    expect(out.note).toBe('ok');
  });

  it('recurses into nested objects and arrays', () => {
    const out = scrubSecrets({ a: { b: ['rk_live_deep0001'] }, token: 'y' }) as any;
    expect(out.a.b[0]).toBe(REDACTED);
    expect(out.token).toBe(REDACTED);
  });

  it('leaves benign values untouched', () => {
    expect(scrubSecrets({ count: 3, ok: true, name: 'work_in_progress' })).toEqual({
      count: 3,
      ok: true,
      name: 'work_in_progress',
    });
  });
});

describe('scrubSentryEvent', () => {
  it('drops the connect endpoint request body entirely', () => {
    const event = {
      request: { url: 'https://app/api/stripe/connect/', data: { credential: 'rk_live_x1234' } },
    };
    const out = scrubSentryEvent(event as any);
    expect(out.request.data).toBe(REDACTED);
  });

  it('scrubs secret values elsewhere in the event', () => {
    const event = { message: 'fail rk_live_inmessage', extra: { d: 'sk_live_inextra' } };
    const out = JSON.stringify(scrubSentryEvent(event as any));
    expect(out).not.toContain('rk_live_inmessage');
    expect(out).not.toContain('sk_live_inextra');
  });

  it('leaves a non-sensitive request body in place', () => {
    const event = { request: { url: 'https://app/api/other/', data: { foo: 'bar' } } };
    const out = scrubSentryEvent(event as any);
    expect(out.request.data).toEqual({ foo: 'bar' });
  });
});

describe('scrubBreadcrumb', () => {
  it('redacts the body of a connect-call breadcrumb', () => {
    const bc = {
      category: 'xhr',
      data: { url: '/api/stripe/connect/', body: 'rk_live_x1234', method: 'POST' },
    };
    const out = scrubBreadcrumb(bc as any);
    expect(out.data.body).toBe(REDACTED);
  });

  it('scrubs secret values in non-connect breadcrumbs', () => {
    const bc = { message: 'token sk_live_crumb1234', data: { url: '/api/other/' } };
    const out = JSON.stringify(scrubBreadcrumb(bc as any));
    expect(out).not.toContain('sk_live_crumb1234');
  });
});
