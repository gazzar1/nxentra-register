/**
 * Sentry scrubbing helpers — keep secrets out of client error telemetry.
 *
 * The Stripe connect form posts a restricted key (rk_…) to /stripe/connect/.
 * With replaysOnErrorSampleRate=1.0 and axios breadcrumbs, an error near the
 * connect call could otherwise carry the live key (request body, breadcrumb
 * data, or a captured error.config.data). These pure functions redact:
 *   - any field whose NAME looks credential-like, anywhere in the payload;
 *   - any string VALUE matching a known secret pattern (rk_/sk_/pk_/whsec_/
 *     shp*_), even under a benign field name; and
 *   - the request body of sensitive endpoints (the connect endpoint) entirely.
 *
 * Kept out of sentry.client.config.ts (which runs Sentry.init on import) so they
 * stay unit-testable.
 */

export const REDACTED = '[redacted]';

const SECRET_KEY_RE =
  /(credential|api[_-]?key|secret|token|password|authorization|access[_-]?key|webhook[_-]?secret|client[_-]?secret)/i;

// Underscores are part of the token body (rk_live_<random>) so the FULL key is
// consumed — matching only `rk_live` would leave the secret random tail exposed.
const SECRET_VALUE_RE = /\b(rk|sk|pk|whsec|shpat|shprt|shpss|shpca)_[A-Za-z0-9_]{4,}/g;

const SENSITIVE_PATHS = ['/stripe/connect/'];

/** Recursively redact secret-named keys and secret-looking string values. */
export function scrubSecrets(value: unknown): unknown {
  if (typeof value === 'string') {
    return value.replace(SECRET_VALUE_RE, REDACTED);
  }
  if (Array.isArray(value)) {
    return value.map(scrubSecrets);
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [
        k,
        SECRET_KEY_RE.test(k) ? REDACTED : scrubSecrets(v),
      ]),
    );
  }
  return value;
}

function urlIsSensitive(url: unknown): boolean {
  return typeof url === 'string' && SENSITIVE_PATHS.some((p) => url.includes(p));
}

/** Sentry `beforeSend` — drop sensitive-endpoint request bodies, then scrub. */
export function scrubSentryEvent<T extends Record<string, any>>(event: T): T {
  const e = event as any;
  if (e?.request && urlIsSensitive(e.request.url) && e.request.data != null) {
    e.request.data = REDACTED;
  }
  return scrubSecrets(event) as T;
}

/** Sentry `beforeBreadcrumb` — redact connect-call breadcrumbs (xhr/fetch). */
export function scrubBreadcrumb<T extends Record<string, any>>(breadcrumb: T): T {
  const b = breadcrumb as any;
  if (b?.data && urlIsSensitive(b.data.url) && 'body' in b.data) {
    b.data = { ...b.data, body: REDACTED };
  }
  return scrubSecrets(breadcrumb) as T;
}
