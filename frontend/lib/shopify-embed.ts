/**
 * B8 / B8.5 (2026-06-06): Shopify App Bridge embedded-mode helpers.
 *
 * When Shopify embeds our app inside the admin iframe, Shopify's CDN
 * script (loaded in _document.tsx) populates `window.shopify` with:
 *   - shopify.config: { apiKey, host, shop, locale, ... }
 *   - shopify.idToken(): Promise<string>   ← session token JWT
 *   - shopify.toast.show(message): for native toasts
 *
 * The script does nothing useful outside the iframe — `window.shopify`
 * stays undefined, and our hooks return null/false. That makes it safe
 * to call them unconditionally from page components.
 *
 * Embedded detection: Shopify launches us at /?host=<base64>&shop=<x>
 * inside an iframe. Presence of the `host` query param is the canonical
 * signal of an embedded launch. After the first embedded page mounts we
 * also persist host/shop to sessionStorage so that subsequent in-iframe
 * navigations (which may not carry the params in the URL) still report
 * "embedded mode" correctly. sessionStorage in a Shopify-framed
 * app.nxentra.com context is partitioned per top-level origin, so the
 * persistence can't leak into a standalone-tab session.
 */

import { useEffect, useState } from "react";

declare global {
  interface Window {
    shopify?: {
      config?: {
        apiKey?: string;
        host?: string;
        shop?: string;
        locale?: string;
      };
      idToken?: () => Promise<string>;
      toast?: { show: (message: string) => void };
      redirect?: {
        toRemote?: (options: { url: string; newContext?: boolean }) => void;
        dispatch?: (action: unknown) => void;
      };
    };
  }
}

const SESSION_HOST_KEY = "nxentra-shopify-host";
const SESSION_SHOP_KEY = "nxentra-shopify-shop";

function safeSessionGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    // sessionStorage can throw in partitioned-storage edge cases.
    return null;
  }
}

function safeSessionSet(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

function safeSessionRemove(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/**
 * Persist the Shopify launch context (host + shop) for the duration of
 * the iframe session. Called once from the embedded landing page so that
 * any subsequent in-iframe navigation that drops the URL params still
 * recognizes itself as embedded.
 */
export function persistShopifyContext(host: string, shop: string): void {
  if (host) safeSessionSet(SESSION_HOST_KEY, host);
  if (shop) safeSessionSet(SESSION_SHOP_KEY, shop);
}

export function clearShopifyContext(): void {
  safeSessionRemove(SESSION_HOST_KEY);
  safeSessionRemove(SESSION_SHOP_KEY);
}

/**
 * Returns true if the current page is running inside the Shopify admin
 * iframe. Checks the URL first (canonical signal), then sessionStorage
 * (set by persistShopifyContext during the embedded launch landing).
 *
 * SSR-safe: returns false during SSR and on first client render before
 * the URL is parsed.
 */
export function useShopifyEmbedded(): boolean {
  const [isEmbedded, setIsEmbedded] = useState(false);

  useEffect(() => {
    setIsEmbedded(isShopifyEmbedded());
  }, []);

  return isEmbedded;
}

/**
 * Synchronous version of useShopifyEmbedded for use inside event
 * handlers or non-React code. Returns false during SSR.
 */
export function isShopifyEmbedded(): boolean {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  if (params.get("host")) return true;
  return Boolean(safeSessionGet(SESSION_HOST_KEY));
}

/**
 * Returns the current Shopify session token (JWT), refreshing it
 * when it's about to expire (session tokens are short-lived — ~60s).
 * Returns null when App Bridge isn't loaded yet, when not embedded,
 * or when token retrieval fails.
 *
 * The token rotates frequently. Callers should fetch a fresh token
 * for every backend call rather than caching it.
 */
export async function getShopifySessionToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  if (!window.shopify?.idToken) return null;
  try {
    const token = await window.shopify.idToken();
    return token || null;
  } catch {
    return null;
  }
}

/**
 * Hook variant of getShopifySessionToken. Re-fetches on mount and
 * exposes a refresh() callback for refreshing on demand (e.g. after
 * a 401 response from the backend).
 */
export function useShopifySessionToken(): {
  token: string | null;
  loading: boolean;
  refresh: () => Promise<void>;
} {
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    const next = await getShopifySessionToken();
    setToken(next);
    setLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    const fetchToken = async () => {
      const next = await getShopifySessionToken();
      if (!cancelled) {
        setToken(next);
        setLoading(false);
      }
    };
    fetchToken();
    return () => {
      cancelled = true;
    };
  }, []);

  return { token, loading, refresh };
}

/**
 * Reads the `shop` query param from the URL, falling back to
 * sessionStorage when the URL doesn't carry it. Returns null only when
 * we have no record of which shop launched us.
 */
export function getShopifyShopParam(): string | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  return params.get("shop") || safeSessionGet(SESSION_SHOP_KEY);
}

/**
 * Reads the `host` param — same fallback as getShopifyShopParam. The
 * `host` value is what App Bridge needs to initialize and what we put
 * back in URLs to keep downstream pages embedded-aware.
 */
export function getShopifyHostParam(): string | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  return params.get("host") || safeSessionGet(SESSION_HOST_KEY);
}

/**
 * B13 (2026-06-07): top-level navigation that survives inside the
 * Shopify admin iframe.
 *
 * Plain `window.location.href = url` navigates the IFRAME itself — fine
 * for same-origin paths, but OAuth screens like
 * `accounts.shopify.com/oauth/authorize` send `X-Frame-Options: DENY`
 * and refuse to render inside an iframe, producing
 * "Firefox can't open this page" / Chrome's equivalent. The merchant
 * gets stuck.
 *
 * App Bridge's `shopify.redirect.toRemote({ url, newContext })` is the
 * sanctioned escape hatch: Shopify breaks out of the iframe at the
 * browser level without needing `allow-top-navigation` in the sandbox,
 * and the redirect_uri returns the merchant either back to the same tab
 * (default) or to a new tab (`newContext: true`).
 *
 * Outside the iframe (standalone Nxentra), this is just a normal
 * navigation — same caller, no branching at the call site.
 *
 * Fallback order, defensive against missing App Bridge:
 *   1. shopify.redirect.toRemote — sanctioned
 *   2. window.open(_top|_blank) — needs sandbox `allow-top-navigation`
 *      (top) or `allow-popups` (blank), both usually granted by Shopify
 *   3. window.location.href — last resort; in iframe this will be
 *      blocked by the destination's X-Frame-Options
 */
export function redirectTopLevel(
  url: string,
  options: { newContext?: boolean } = {},
): void {
  if (typeof window === "undefined") return;
  const newContext = options.newContext ?? false;

  if (!isShopifyEmbedded()) {
    if (newContext) {
      window.open(url, "_blank", "noopener,noreferrer");
    } else {
      window.location.href = url;
    }
    return;
  }

  // B17.1 (2026-06-07): split the embedded-mode strategy by intent.
  //
  // For newContext (open in a new top-level tab — e.g. the "Open Nxentra"
  // button from the no_connection iframe state): use plain window.open
  // directly. Live test 2026-06-07 showed that
  // shopify.redirect.toRemote({ url, newContext: true }) opens the new
  // tab AND also re-iframes our own application_url through the admin
  // shell's app router as a side effect — the merchant ended up with
  // both a new register tab AND the iframe pointed at the register
  // page. Shopify's iframe sandbox grants `allow-popups`, so plain
  // window.open is a clean one-effect operation.
  //
  // For !newContext (same-tab top-level navigation — e.g. OAuth start):
  // we still need App Bridge's sanctioned escape because navigating the
  // iframe to accounts.shopify.com fails with X-Frame-Options: DENY.
  if (newContext) {
    const opened = window.open(url, "_blank", "noopener,noreferrer");
    if (opened) return;
    // Popup blocked — last-ditch App Bridge fallback. Not preferred
    // because of the side effect, but better than silently dropping the
    // navigation.
    try {
      window.shopify?.redirect?.toRemote?.({ url, newContext: true });
    } catch {
      /* nothing else we can safely do here */
    }
    return;
  }

  try {
    if (window.shopify?.redirect?.toRemote) {
      window.shopify.redirect.toRemote({ url });
      return;
    }
  } catch {
    /* fall through */
  }

  try {
    const opened = window.open(url, "_top");
    if (opened) return;
  } catch {
    /* fall through */
  }

  window.location.href = url;
}
