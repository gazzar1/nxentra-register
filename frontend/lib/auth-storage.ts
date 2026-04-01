/**
 * Auth state management.
 *
 * Tokens are now stored in HttpOnly cookies (set by the backend).
 * This module manages a lightweight "authenticated" flag in localStorage
 * so the frontend knows whether to attempt API calls on page load.
 *
 * The flag is NOT a security boundary — the real auth check is the
 * cookie validity on the server. This is just a UI hint.
 */

const AUTH_FLAG_KEY = "nxentra_authenticated";

// Legacy token keys — used only for migration cleanup
const LEGACY_ACCESS_KEY = "nxentra_access";
const LEGACY_REFRESH_KEY = "nxentra_refresh";

export function isAuthenticated(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(AUTH_FLAG_KEY) === "true";
}

export function setAuthenticated(value: boolean): void {
  if (typeof window === "undefined") return;
  if (value) {
    localStorage.setItem(AUTH_FLAG_KEY, "true");
  } else {
    localStorage.removeItem(AUTH_FLAG_KEY);
  }
}

/**
 * Clean up legacy localStorage tokens from the pre-cookie auth flow.
 * Call once during app initialization to ensure old tokens are removed.
 */
export function cleanupLegacyTokens(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(LEGACY_ACCESS_KEY);
  localStorage.removeItem(LEGACY_REFRESH_KEY);
}

// ─── Backward compatibility aliases ─────────────────────────────────────────
// These are kept so existing imports don't break during migration.
// They now delegate to the cookie-based flow.

/** @deprecated Tokens are now in HttpOnly cookies. Use isAuthenticated() instead. */
export function getAccessToken(): string | null {
  // Return the auth flag as a truthy indicator — actual token is in cookie
  return isAuthenticated() ? "__cookie__" : null;
}

/** @deprecated Tokens are now in HttpOnly cookies. */
export function getRefreshToken(): string | null {
  return isAuthenticated() ? "__cookie__" : null;
}

/** @deprecated Tokens are now set by the backend as HttpOnly cookies. */
export function storeTokens(_access: string, _refresh: string): void {
  setAuthenticated(true);
}

/** @deprecated Cookies are cleared by the backend on logout. */
export function removeTokens(): void {
  setAuthenticated(false);
}

export const removeAccessToken = removeTokens;
