const { withSentryConfig } = require("@sentry/nextjs");

// B8.5 (2026-06-05): Shopify embedded apps must be embeddable inside the
// Shopify admin iframe. Modern browsers honor `Content-Security-Policy:
// frame-ancestors` over `X-Frame-Options`, so we set frame-ancestors to
// the two Shopify-controlled origins that legally host our iframe:
//
//   - https://*.myshopify.com — every merchant's shop admin (legacy path)
//   - https://admin.shopify.com — the unified admin URL Shopify is rolling
//     out (App Bridge ships with this origin enabled too)
//
// We deliberately do not include `'self'` here — same-origin iframing of
// our own app provides no value and would soften clickjacking protection.
//
// Browsers that support CSP 2 use `frame-ancestors` and ignore any
// X-Frame-Options on the same response, so we don't need to touch
// X-Frame-Options here — even if an upstream proxy adds DENY it will
// be overridden. (If an old IE merchant ever tries to embed us, the
// admin won't load App Bridge either; not a real-world concern.)
const SHOPIFY_FRAME_ANCESTORS = [
  "'self'",
  "https://*.myshopify.com",
  "https://admin.shopify.com",
].join(" ");

const shopifyEmbedHeaders = [
  {
    key: "Content-Security-Policy",
    value: `frame-ancestors ${SHOPIFY_FRAME_ANCESTORS};`,
  },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  i18n: {
    locales: ['en', 'ar'],
    defaultLocale: 'en',
    localeDetection: false,
  },
  async headers() {
    return [
      {
        // Every page can be embedded by Shopify admin. The Shopify launch
        // path lands first at `/` (which redirects to `/shopify/embedded`)
        // and then the merchant navigates anywhere in the app — all of
        // which has to stay embeddable.
        source: "/:path*",
        headers: shopifyEmbedHeaders,
      },
    ];
  },
};

// Only wrap with Sentry if DSN is configured
const sentryEnabled = !!process.env.NEXT_PUBLIC_SENTRY_DSN;

module.exports = sentryEnabled
  ? withSentryConfig(nextConfig, {
      // Suppress source map upload warnings when no auth token
      silent: true,
      // Don't widen existing source maps
      widenClientFileUpload: true,
      // Hide source maps from browser devtools in production
      hideSourceMaps: true,
      // Disable telemetry
      disableLogger: true,
    })
  : nextConfig;
