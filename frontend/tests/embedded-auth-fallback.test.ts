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

  it('blocks an ordinary embedded request (fail closed) when no fresh session token', async () => {
    getShopifySessionToken.mockResolvedValue(null);
    await expect(
      runRequestInterceptor({ method: 'get', url: '/shopify/orders', headers: {} }),
    ).rejects.toBeTruthy();
  });

  it('an ambient Nxentra cookie cannot rescue a missing session token', async () => {
    document.cookie = 'nxentra_access=ambient-jwt';
    getShopifySessionToken.mockResolvedValue(null);
    await expect(
      runRequestInterceptor({ method: 'get', url: '/shopify/orders', headers: {} }),
    ).rejects.toBeTruthy();
  });

  it('token-exchange receives a fresh Shopify bearer (no longer a bootstrap call)', async () => {
    getShopifySessionToken.mockResolvedValue('fresh-session-token');
    const cfg = await runRequestInterceptor({ method: 'post', url: '/shopify/token-exchange/', headers: {} });
    expect(cfg.headers.Authorization).toBe('Bearer fresh-session-token');
  });

  it('never attaches auth for the remaining unauthenticated bootstrap endpoints', async () => {
    getShopifySessionToken.mockResolvedValue('fresh-session-token');
    for (const url of ['/auth/shopify-session-login/', '/shopify/redeem-linking-nonce/']) {
      const cfg = await runRequestInterceptor({ method: 'post', url, headers: {} });
      expect(cfg.headers.Authorization).toBeUndefined();
    }
  });
});
