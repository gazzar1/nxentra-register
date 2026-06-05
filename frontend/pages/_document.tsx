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
          App Bridge MUST come before any other <script> in <head>. The
          meta tag goes immediately above. No async / defer / type=module.
        */}
        <meta name="shopify-api-key" content={shopifyApiKey} />
        <script src="https://cdn.shopify.com/shopifycloud/app-bridge.js" />
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
