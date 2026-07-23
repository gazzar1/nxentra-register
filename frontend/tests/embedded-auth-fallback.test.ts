import { describe, it, expect, vi, beforeEach } from 'vitest';

// A1: verify the embedded request interceptor is session-token-only by DEFAULT
// (no silent fallback to the stored exchanged Nxentra JWT), and that the
// exchanged fallback is opt-in via NEXT_PUBLIC_ENABLE_EXCHANGED_TOKEN_FALLBACK.

const getShopifySessionToken = vi.fn();

vi.mock('@/lib/shopify-embed', () => ({
  isShopifyEmbedded: () => true,
  getShopifySessionToken: () => getShopifySessionToken(),
}));
vi.mock('@/lib/embedded-auth', () => ({
  getEmbeddedAccessToken: () => 'stored-exchanged-jwt',
  setEmbeddedAccessToken: vi.fn(),
  clearEmbeddedAccessToken: vi.fn(),
}));
vi.mock('@/lib/auth-storage', () => ({ setAuthenticated: vi.fn() }));

import apiClient from '@/lib/api-client';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function runRequestInterceptor(config: any) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handler = (apiClient.interceptors.request as any).handlers[0];
  return handler.fulfilled(config);
}

describe('embedded auth: session-token-only by default', () => {
  beforeEach(() => {
    getShopifySessionToken.mockReset();
    delete process.env.NEXT_PUBLIC_ENABLE_EXCHANGED_TOKEN_FALLBACK;
  });

  it('attaches a fresh App Bridge session token', async () => {
    getShopifySessionToken.mockResolvedValue('fresh-session-token');
    const cfg = await runRequestInterceptor({ method: 'get', url: '/shopify/orders', headers: {} });
    expect(cfg.headers.Authorization).toBe('Bearer fresh-session-token');
  });

  it('does NOT fall back to the stored exchanged JWT when no session token (default)', async () => {
    getShopifySessionToken.mockResolvedValue(null);
    const cfg = await runRequestInterceptor({ method: 'get', url: '/shopify/orders', headers: {} });
    expect(cfg.headers.Authorization).toBeUndefined();
  });

  it('never attaches auth for the auth-bootstrap endpoints', async () => {
    getShopifySessionToken.mockResolvedValue('fresh-session-token');
    for (const url of [
      '/auth/shopify-session-login/',
      '/shopify/token-exchange/',
      '/shopify/redeem-linking-nonce/',
    ]) {
      const cfg = await runRequestInterceptor({ method: 'post', url, headers: {} });
      expect(cfg.headers.Authorization).toBeUndefined();
    }
  });
});
