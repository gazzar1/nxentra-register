import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import { setAuthenticated } from './auth-storage';
import {
  clearEmbeddedAccessToken,
  getEmbeddedAccessToken,
  setEmbeddedAccessToken,
} from './embedded-auth';
import { getShopifySessionToken, isShopifyEmbedded } from './shopify-embed';

// Extend AxiosRequestConfig to include _retry flag
interface CustomAxiosRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

const baseURL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api';

const apiClient = axios.create({
  baseURL,
  // withCredentials still on for non-embedded flows (standard browser
  // session). In embedded mode the cookies are sent too but the browser
  // ignores them under SameSite=Strict/Lax — we authenticate via the
  // Authorization header instead (set below in the request interceptor).
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * Read the CSRF token from the csrftoken cookie (non-HttpOnly, set by Django).
 */
function getCsrfToken(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : null;
}

/**
 * A1 (2026-07-23): seed the Django `csrftoken` cookie for the standalone
 * browser double-submit CSRF flow. Login already seeds it server-side; this
 * covers an already-logged-in session or a cold SPA load. Embedded mode
 * authenticates via a Bearer header (CSRF-exempt) and needs no CSRF cookie.
 */
export async function ensureCsrfToken(): Promise<void> {
  if (typeof document === 'undefined') return;
  if (isShopifyEmbedded()) return;
  if (getCsrfToken()) return;
  try {
    await apiClient.get('/auth/csrf/');
  } catch {
    // Best-effort — login also seeds the cookie; a missing token only causes
    // the first mutation to 403, after which the SPA can retry.
  }
}

// Request interceptor — CSRF for non-GET (standalone), Bearer auth when embedded.
apiClient.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    if (config.method && config.method !== 'get') {
      const csrfToken = getCsrfToken();
      if (csrfToken) {
        config.headers['X-CSRFToken'] = csrfToken;
      }
    }

    // A1: inside the Shopify admin iframe we authenticate via a FRESH App Bridge
    // session token — the backend verifies it per-request against the explicit
    // (store, sub) binding. A fresh token reflects *current* Shopify
    // authorization: a revoked merchant cannot mint one, so authorization loss
    // is not concealed. Ordinary requests do NOT fall back to the stored,
    // exchanged Nxentra JWT (which would keep a revoked merchant working until
    // it expired); the exchanged token is a recovery-only path, gated by the G1
    // switch below. Never attach for the auth-bootstrap calls themselves.
    if (isShopifyEmbedded()) {
      const url = config.url || '';
      const isAuthBootstrap =
        url.includes('/auth/shopify-session-login') ||
        url.includes('/shopify/token-exchange') ||
        url.includes('/shopify/redeem-linking-nonce');
      if (!isAuthBootstrap) {
        let bearer: string | null = null;
        try {
          bearer = await getShopifySessionToken();
        } catch {
          bearer = null;
        }
        // Recovery only, and only while the exchanged-token fallback is enabled
        // (App Bridge not yet loaded on a cold iframe reload).
        if (!bearer && !exchangedFallbackDisabled()) {
          bearer = getEmbeddedAccessToken();
        }
        if (bearer) {
          config.headers['Authorization'] = `Bearer ${bearer}`;
        }
      }
    }

    return config;
  },
  (error) => Promise.reject(error)
);

/**
 * G1 switch: when NEXT_PUBLIC_DISABLE_EXCHANGED_TOKEN_FALLBACK === 'true', the
 * exchanged Nxentra-JWT fallback is disabled entirely, so embedded requests
 * must authenticate through fresh Shopify session tokens. Used to prove the
 * session-token path in the real iframe with third-party cookies disabled, and
 * to guarantee that loss of Shopify authorization is not concealed by a stored
 * Nxentra JWT.
 */
function exchangedFallbackDisabled(): boolean {
  return process.env.NEXT_PUBLIC_DISABLE_EXCHANGED_TOKEN_FALLBACK === 'true';
}

/**
 * Poll App Bridge for a fresh Shopify session token (up to 5s — the CDN may not
 * have finished loading on a hard iframe reload). Returns null if none.
 */
async function pollForSessionToken(): Promise<string | null> {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const token = await getShopifySessionToken();
    if (token) return token;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return null;
}

/**
 * B8.5: re-mint a Nxentra JWT inside the iframe by calling App Bridge for
 * a fresh session token and POSTing it to /auth/shopify-session-login/.
 * Returns the new access token, or null if any step fails.
 *
 * Polls for App Bridge for up to 5s. On a hard page reload inside the
 * iframe, the api-client may attempt a request before App Bridge has
 * finished loading from the CDN — without the poll, the first 401 retry
 * would fall through to "unauthenticated" even though App Bridge is
 * milliseconds away from being ready.
 *
 * Coalescing: if multiple requests 401 at once, they all share the same
 * in-flight refresh promise so we only call session-login once.
 */
let inFlightRefresh: Promise<string | null> | null = null;

async function refreshEmbeddedSession(): Promise<string | null> {
  if (inFlightRefresh) return inFlightRefresh;
  inFlightRefresh = (async () => {
    try {
      const deadline = Date.now() + 5000;
      let sessionToken: string | null = null;
      while (Date.now() < deadline) {
        sessionToken = await getShopifySessionToken();
        if (sessionToken) break;
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      if (!sessionToken) return null;
      const { data } = await axios.post<{ access: string; refresh: string }>(
        `${baseURL}/auth/shopify-session-login/`,
        { session_token: sessionToken },
        { withCredentials: true, headers: { 'Content-Type': 'application/json' } }
      );
      if (data?.access) {
        setEmbeddedAccessToken(data.access);
        return data.access;
      }
      return null;
    } catch {
      return null;
    } finally {
      inFlightRefresh = null;
    }
  })();
  return inFlightRefresh;
}

// Response interceptor - handle token refresh and tenant context errors
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as CustomAxiosRequestConfig;
    const errorData = error.response?.data as { detail?: string } | undefined;

    // Handle missing tenant context - redirect to company selection.
    // In embedded mode we don't have a select-company UI inside the iframe,
    // so swallow the redirect and let the page show its own error state.
    if (error.response?.status === 403 && errorData?.detail === 'no_tenant_context') {
      if (typeof window !== 'undefined' && !isShopifyEmbedded()) {
        window.location.href = '/select-company';
      }
      return Promise.reject(error);
    }

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true;

      // A1: in the Shopify admin iframe, retry with a FRESH session token first
      // (primary path — reflects current Shopify authorization). Only if that
      // fails AND the exchanged-token fallback is enabled do we fall back to a
      // session-login exchange (recovery). With the G1 switch on, a revoked
      // merchant's request fails here instead of being concealed by a stored JWT.
      if (isShopifyEmbedded()) {
        const freshSession = await pollForSessionToken();
        if (freshSession) {
          originalRequest.headers = originalRequest.headers || {};
          originalRequest.headers['Authorization'] = `Bearer ${freshSession}`;
          return apiClient(originalRequest);
        }
        if (!exchangedFallbackDisabled()) {
          const fresh = await refreshEmbeddedSession();
          if (fresh) {
            originalRequest.headers = originalRequest.headers || {};
            originalRequest.headers['Authorization'] = `Bearer ${fresh}`;
            return apiClient(originalRequest);
          }
        }
        clearEmbeddedAccessToken();
        setAuthenticated(false);
        // Inside the iframe we can't usefully redirect to /login (it
        // would be denied embedding). Let the page surface the error.
        return Promise.reject(error);
      }

      // Standalone (non-embedded) flow: cookie-based refresh.
      try {
        await axios.post(`${baseURL}/auth/refresh/`, {}, { withCredentials: true });
        return apiClient(originalRequest);
      } catch (refreshError) {
        setAuthenticated(false);
        if (typeof window !== 'undefined') {
          window.location.href = '/login';
        }
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

export default apiClient;

// Helper to extract error message from API response
export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data;
    if (typeof data === 'string') {
      return data;
    }
    if (data?.error) {
      return data.error;
    }
    if (data?.detail) {
      return data.detail;
    }
    if (data?.message) {
      return data.message;
    }
    // Handle validation errors
    if (data && typeof data === 'object') {
      const firstKey = Object.keys(data)[0];
      if (firstKey && Array.isArray(data[firstKey])) {
        return `${firstKey}: ${data[firstKey][0]}`;
      }
    }
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'An unexpected error occurred';
}
