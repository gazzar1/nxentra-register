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

// Request interceptor — CSRF for non-GET, Bearer auth when embedded.
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    if (config.method && config.method !== 'get') {
      const csrfToken = getCsrfToken();
      if (csrfToken) {
        config.headers['X-CSRFToken'] = csrfToken;
      }
    }

    // B8.5: inside the Shopify admin iframe, cookies are blocked. Attach
    // the Nxentra JWT obtained from `/auth/shopify-session-login/` as a
    // Bearer header. Do not attach for the session-login or token-exchange
    // calls themselves — those bootstrap the auth state.
    if (isShopifyEmbedded()) {
      const tok = getEmbeddedAccessToken();
      const url = config.url || '';
      const isAuthBootstrap =
        url.includes('/auth/shopify-session-login') ||
        url.includes('/shopify/token-exchange');
      if (tok && !isAuthBootstrap) {
        config.headers['Authorization'] = `Bearer ${tok}`;
      }
    }

    return config;
  },
  (error) => Promise.reject(error)
);

/**
 * B8.5: re-mint a Nxentra JWT inside the iframe by calling App Bridge for
 * a fresh session token and POSTing it to /auth/shopify-session-login/.
 * Returns the new access token, or null if any step fails.
 */
async function refreshEmbeddedSession(): Promise<string | null> {
  try {
    const sessionToken = await getShopifySessionToken();
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
  }
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

      // B8.5: in the Shopify admin iframe, cookies don't ride along, so
      // /auth/refresh/ would always fail. Mint a fresh access token via
      // App Bridge -> session-login instead.
      if (isShopifyEmbedded()) {
        const fresh = await refreshEmbeddedSession();
        if (fresh) {
          originalRequest.headers = originalRequest.headers || {};
          originalRequest.headers['Authorization'] = `Bearer ${fresh}`;
          return apiClient(originalRequest);
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
