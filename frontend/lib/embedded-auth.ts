/**
 * B8.5 (2026-06-05): in-memory Nxentra JWT for embedded Shopify launches.
 *
 * In the Shopify admin iframe, our HttpOnly auth cookies are dropped on
 * every request because SameSite blocks cross-site cookie transmission.
 * The embedded landing page (`/shopify/embedded`) calls
 * `/api/auth/shopify-session-login/` and stores the returned access token
 * here; the API client (`lib/api-client.ts`) reads it and attaches it as
 * `Authorization: Bearer ...` on every embedded-mode request.
 *
 * Why memory and not storage:
 *   - localStorage is shared across the merchant's whole shopify admin —
 *     we'd leak tokens between apps.
 *   - sessionStorage is iframe-scoped and survives in-tab reloads, but
 *     we'd still have to invalidate it on logout.
 *   - In-memory is simpler and safer; an iframe reload just costs one
 *     extra round-trip to App Bridge + session-login (~1s) which is the
 *     same cost the embedded landing page already pays.
 *
 * The api-client refresh path re-issues a session-login on 401, so a
 * lost token is recovered transparently.
 */

let embeddedAccessToken: string | null = null;

export function setEmbeddedAccessToken(token: string | null): void {
  embeddedAccessToken = token || null;
}

export function getEmbeddedAccessToken(): string | null {
  return embeddedAccessToken;
}

export function clearEmbeddedAccessToken(): void {
  embeddedAccessToken = null;
}
