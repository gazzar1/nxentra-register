import * as Sentry from "@sentry/nextjs";
import { scrubBreadcrumb, scrubSentryEvent } from "@/lib/sentry-scrub";

const SENTRY_DSN = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 1.0,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || "production",
    release: process.env.NEXT_PUBLIC_APP_VERSION || "dev",

    // Don't send PII
    sendDefaultPii: false,

    // Redact provider credentials (Stripe rk_ keys, webhook secrets, tokens)
    // before any event/breadcrumb leaves the browser — the connect form posts a
    // live key and replaysOnErrorSampleRate is 1.0. See lib/sentry-scrub.ts.
    beforeSend: (event) => scrubSentryEvent(event),
    beforeBreadcrumb: (breadcrumb) => scrubBreadcrumb(breadcrumb),

    // Ignore common non-actionable errors
    ignoreErrors: [
      "ResizeObserver loop",
      "Non-Error promise rejection",
      "Load failed",
      "Failed to fetch",
      "NetworkError",
      "AbortError",
    ],
  });
}
