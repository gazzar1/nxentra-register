import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// A1: when the embedded landing page hits `no_connection`, it must open
// standalone Nxentra in a NEW FIRST-PARTY context (redirectTopLevel with
// newContext:true) — NOT navigate the iframe to /login (which keeps
// isShopifyEmbedded() true, skips ensureCsrfToken, and breaks the now
// CSRF-required browser login, fatally so with third-party cookies disabled).

const mockAxiosPost = vi.fn();
const redirectTopLevel = vi.fn();

vi.mock('axios', () => ({
  default: { post: (...a: unknown[]) => mockAxiosPost(...a) },
}));
vi.mock('@/lib/api-client', () => ({ default: { post: vi.fn() } }));
vi.mock('@/lib/embedded-auth', () => ({ setEmbeddedAccessToken: vi.fn() }));
vi.mock('@/lib/auth-storage', () => ({ setAuthenticated: vi.fn() }));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ refreshProfile: vi.fn(() => Promise.resolve()) }),
}));
vi.mock('next/router', () => ({
  useRouter: () => ({ isReady: true, replace: vi.fn(), reload: vi.fn(), query: {} }),
}));
vi.mock('next-i18next/serverSideTranslations', () => ({ serverSideTranslations: vi.fn() }));
vi.mock('@/lib/shopify-embed', () => ({
  isShopifyEmbedded: () => true,
  getShopifySessionToken: () => Promise.resolve('session-tok'),
  getShopifyShopParam: () => 'merchant.myshopify.com',
  getShopifyHostParam: () => 'host123',
  persistShopifyContext: vi.fn(),
  redirectTopLevel: (...a: unknown[]) => redirectTopLevel(...a),
}));

import ShopifyEmbeddedPage from '@/pages/shopify/embedded';

describe('embedded no_connection', () => {
  it('opens standalone Nxentra in a new first-party context', async () => {
    mockAxiosPost.mockRejectedValueOnce({
      response: { status: 404, data: { detail: 'no_connection' } },
    });

    render(<ShopifyEmbeddedPage />);

    const btn = await screen.findByText('Open Nxentra');
    fireEvent.click(btn);

    expect(redirectTopLevel).toHaveBeenCalledWith(
      expect.stringContaining('/login'),
      { newContext: true },
    );
  });
});
