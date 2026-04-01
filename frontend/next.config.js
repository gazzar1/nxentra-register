const { withSentryConfig } = require("@sentry/nextjs");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  i18n: {
    locales: ['en', 'ar'],
    defaultLocale: 'en',
    localeDetection: false,
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
