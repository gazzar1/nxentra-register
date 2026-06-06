/**
 * B8.5 (2026-06-06): Nxentra JWT storage for embedded Shopify launches.
 *
 * In the Shopify admin iframe, our HttpOnly auth cookies are dropped on
 * every request because SameSite blocks cross-site cookie transmission.
 * The embedded landing page (`/shopify/embedded`) calls
 * `/api/auth/shopify-session-login/` and stores the returned access token
 * here; the API client (`lib/api-client.ts`) reads it and attaches it as
 * `Authorization: Bearer ...` on every embedded-mode request.
 *
 * Storage layers:
 *   - Module memory (primary, fast)
 *   - sessionStorage (survives in-iframe reloads and back/forward navs)
 *
 * Why sessionStorage and not localStorage:
 *   - localStorage is shared across the merchant's whole shopify admin
 *     and the same browser elsewhere, so a token leak could affect
 *     other apps or top-level sessions.
 *   - sessionStorage in a Shopify-framed app.nxentra.com context is
 *     partitioned per top-level origin and is wiped when the iframe
 *     (or its admin tab) closes. That matches the lifetime of the
 *     embedded session itself.
 *
 * The api-client's 401 retry path re-issues a session-login when the
 * token is missing or invalid, so an evicted token recovers transparently.
 */

const SESSION_KEY = "nxentra-embedded-access-token";

let embeddedAccessToken: string | null = null;

function safeSessionGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
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

export function setEmbeddedAccessToken(token: string | null): void {
  embeddedAccessToken = token || null;
  if (token) {
    safeSessionSet(SESSION_KEY, token);
  } else {
    safeSessionRemove(SESSION_KEY);
  }
}

export function getEmbeddedAccessToken(): string | null {
  if (embeddedAccessToken) return embeddedAccessToken;
  // Page reload or back/forward nav wiped module memory — recover from
  // sessionStorage. Repopulate memory so subsequent reads are fast.
  const fromSession = safeSessionGet(SESSION_KEY);
  if (fromSession) {
    embeddedAccessToken = fromSession;
    return fromSession;
  }
  return null;
}

export function clearEmbeddedAccessToken(): void {
  embeddedAccessToken = null;
  safeSessionRemove(SESSION_KEY);
}
