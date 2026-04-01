import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import { setAuthenticated } from './auth-storage';

// Extend AxiosRequestConfig to include _retry flag
interface CustomAxiosRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

const baseURL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api';

const apiClient = axios.create({
  baseURL,
  withCredentials: true, // Send HttpOnly cookies automatically
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

// Request interceptor - add CSRF token for state-changing requests
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // Add CSRF token for non-GET requests (Django requires this for cookie-based auth)
    if (config.method && config.method !== 'get') {
      const csrfToken = getCsrfToken();
      if (csrfToken) {
        config.headers['X-CSRFToken'] = csrfToken;
      }
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor - handle token refresh and tenant context errors
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as CustomAxiosRequestConfig;
    const errorData = error.response?.data as { detail?: string } | undefined;

    // Handle missing tenant context - redirect to company selection
    if (error.response?.status === 403 && errorData?.detail === 'no_tenant_context') {
      if (typeof window !== 'undefined') {
        window.location.href = '/select-company';
      }
      return Promise.reject(error);
    }

    // If 401 and we haven't retried yet — attempt cookie-based refresh
    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true;

      try {
        // Refresh endpoint reads the refresh token from HttpOnly cookie
        await axios.post(`${baseURL}/auth/refresh/`, {}, { withCredentials: true });
        // Cookie is now refreshed — retry original request
        return apiClient(originalRequest);
      } catch (refreshError) {
        // Refresh failed — session expired
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
