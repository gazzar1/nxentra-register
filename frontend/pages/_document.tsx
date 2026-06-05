import { Html, Head, Main, NextScript } from 'next/document';
import type { DocumentProps } from 'next/document';

export default function Document(props: DocumentProps) {
  const locale = props.__NEXT_DATA__.locale || 'en';
  const dir = locale === 'ar' ? 'rtl' : 'ltr';

  // B8 (2026-06-05): Shopify App Bridge. When Shopify embeds our app
  // inside the admin iframe, the script exposes `window.shopify` with
  // `.idToken()` for getting session tokens and `.config` for app info.
  // Outside the iframe (standalone Nxentra access) the script no-ops.
  // The meta tag tells App Bridge our client_id so it can validate
  // session tokens for our app specifically.
  //
  // Shopify enforces strict loading rules: the App Bridge script must
  // appear as the *first* <script> in <head> and MUST be loaded
  // synchronously (no async, defer, or type=module). Any of those flags
  // cause App Bridge to abort initialization with a console error.
  // The synchronous load is fine performance-wise — App Bridge is a
  // small CDN file with aggressive caching.
  const shopifyApiKey = process.env.NEXT_PUBLIC_SHOPIFY_API_KEY || "2258d6303a3672a381fe7606c2d2917b";

  return (
    <Html lang={locale} dir={dir}>
      <Head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+Arabic:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
        {/*
          B8 (2026-06-05): App Bridge requires synchronous loading as
          the first <script> in <head>. Two prior approaches failed:
            - A plain <script src="..."> tag inside Next.js's <Head>
              gets silently stripped by React 18 (no async/defer means
              "potentially blocking", which React refuses to render).
            - next/script with strategy="beforeInteractive" renders the
              tag but auto-injects defer="", which Shopify's App Bridge
              rejects with the same error as async.
          The reliable workaround: render an inline loader (via
          dangerouslySetInnerHTML, which React passes through verbatim)
          that creates the App Bridge <script> element via DOM API and
          inserts it at document.head.firstChild with async=false.

          The loader also gates on the presence of ?shop= AND ?host=
          in the URL — App Bridge auto-initializes when it loads and
          errors with "missing required configuration fields: shop"
          when those URL params are missing. On the standalone /
          marketing page and any other non-Shopify-launched route we
          don't want App Bridge loaded at all.
        */}
        <meta name="shopify-api-key" content={shopifyApiKey} />
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function () {
                try {
                  var p = new URLSearchParams(window.location.search);
                  if (!p.get('shop') || !p.get('host')) return;
                  var s = document.createElement('script');
                  s.src = 'https://cdn.shopify.com/shopifycloud/app-bridge.js';
                  s.async = false;
                  document.head.insertBefore(s, document.head.firstChild);
                } catch (e) {}
              })();
            `,
          }}
        />
        <meta name="description" content="Nxentra - Financial Truth Engine" />
        <link rel="icon" href="/favicon.ico" sizes="any" />
        <link rel="icon" type="image/png" sizes="192x192" href="/android-chrome-192x192.png" />
        <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
      </Head>
      <body>
        {/* Prevent flash of wrong theme */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                try {
                  var theme = localStorage.getItem('nxentra-theme');
                  if (theme === 'light') {
                    document.documentElement.classList.add('light');
                  }
                } catch (e) {}
              })();
            `,
          }}
        />
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}
