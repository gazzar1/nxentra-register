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
