/**
 * B8 (2026-06-05): Shopify App Bridge embedded-mode helpers.
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
 * signal that we should treat this as an embedded launch.
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
    };
  }
}

/**
 * Returns true if the current page was launched from inside Shopify
 * admin. Reads the `?host=` query param synchronously from
 * `window.location`, so this is SSR-safe (returns false during SSR
 * and on first client render before the URL is parsed).
 *
 * Idempotent: safe to call multiple times. Cheap (string parsing).
 */
export function useShopifyEmbedded(): boolean {
  const [isEmbedded, setIsEmbedded] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    setIsEmbedded(Boolean(params.get("host")));
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
  return Boolean(params.get("host"));
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
 * Reads the `shop` query param from the current URL. Shopify always
 * sends this on launch (e.g. `shop=foo.myshopify.com`). Useful for
 * showing the merchant which store they're connecting before the
 * token exchange completes.
 */
export function getShopifyShopParam(): string | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  return params.get("shop");
}
