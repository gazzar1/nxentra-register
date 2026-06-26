/**
 * Stripe Connect (ADR-0002 S1) — service contract + connect-form wiring.
 *
 * Phase 1 self-serve connect: the merchant pastes a restricted READ key
 * (rk_…); the form POSTs it to /stripe/connect/ and surfaces the backend's
 * verbatim 400 message (the backend rejects sk_/pk_ and under-scoped keys
 * with a clear, user-facing hint). No OAuth round-trip in Phase 1.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

const { mockToast, accountsMock } = vi.hoisted(() => ({
  mockToast: vi.fn(),
  // Mutable holder so each describe block can vary what useAccounts() returns
  // (the accounts list that feeds the mapping dropdowns) without re-mocking.
  accountsMock: { current: { data: [] as any[], refetch: vi.fn() } },
}));

// Mock ONLY the axios client; keep the REAL getErrorMessage so the
// verbatim-error surfacing is exercised end-to-end (not stubbed).
vi.mock('@/lib/api-client', async (importActual) => {
  const actual = await importActual<typeof import('@/lib/api-client')>();
  return {
    ...actual,
    default: {
      get: vi.fn().mockResolvedValue({ data: {} }),
      post: vi.fn().mockResolvedValue({ data: {} }),
      put: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    },
  };
});

// Page-level deps (kept thin so the test exercises the real form wiring).
vi.mock('@/components/layout', () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/common', () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock('@/components/ui/toaster', () => ({ useToast: () => ({ toast: mockToast }) }));
vi.mock('@/queries/useAccounts', () => ({ useAccounts: () => accountsMock.current }));
vi.mock('next-i18next/serverSideTranslations', () => ({
  serverSideTranslations: vi.fn().mockResolvedValue({}),
}));

import apiClient from '@/lib/api-client';
import StripeSettingsPage from '@/pages/stripe/settings';

const client = vi.mocked(apiClient);

// ── Service contract ─────────────────────────────────────────────
describe('stripeService.connect contract', () => {
  beforeEach(() => vi.clearAllMocks());

  it('connect(key) → POST /stripe/connect/ with {credential}', async () => {
    const { stripeService } = await import('@/services/stripe.service');
    await stripeService.connect('rk_test_abc123');
    expect(client.post).toHaveBeenCalledWith('/stripe/connect/', {
      credential: 'rk_test_abc123',
    });
  });
});

// ── Connect-form page wiring (not-connected branch) ──────────────
function mockGetEndpoints() {
  client.get.mockImplementation((url: string) => {
    if (url === '/stripe/account/') return Promise.resolve({ data: { connected: false } });
    if (url === '/stripe/account-mapping/') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: {} });
  });
}

const accountGetCount = () =>
  client.get.mock.calls.filter((c: unknown[]) => c[0] === '/stripe/account/').length;
const mappingGetCount = () =>
  client.get.mock.calls.filter((c: unknown[]) => c[0] === '/stripe/account-mapping/').length;

describe('Stripe connect form (not connected)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetEndpoints();
  });

  it('renders a password-masked rk_ key input + Connect button + read-key hint', async () => {
    render(<StripeSettingsPage />);
    const input = (await screen.findByLabelText(/restricted api key/i)) as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.type).toBe('password');
    // A secret field must not be autofilled/cached by the browser.
    expect(input.getAttribute('autocomplete')).toBe('off');
    expect(input.getAttribute('placeholder')).toContain('rk_');
    expect(screen.getByRole('button', { name: /connect/i })).toBeInTheDocument();
    // The how-to hint names the read scopes the backend probe requires.
    expect(screen.getByText(/Balance/)).toBeInTheDocument();
    expect(screen.getByText(/Payouts/)).toBeInTheDocument();
  });

  it('submits the typed key to /stripe/connect/ and shows a success toast', async () => {
    client.post.mockResolvedValueOnce({
      data: {
        connected: true,
        stripe_account_id: 'acct_1',
        status: 'ACTIVE',
        livemode: false,
        display_name: 'Acme',
      },
    });
    render(<StripeSettingsPage />);
    const input = (await screen.findByLabelText(/restricted api key/i)) as HTMLInputElement;
    // One account GET fired on mount; the post-connect reload must fire another.
    await waitFor(() => expect(accountGetCount()).toBe(1));
    fireEvent.change(input, { target: { value: 'rk_live_secret123' } });
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/stripe/connect/', {
        credential: 'rk_live_secret123',
      });
    });
    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith(
        expect.objectContaining({ title: expect.stringMatching(/connected/i) }),
      );
    });
    // The reload (loadAccount) briefly toggles the loading spinner, which
    // remounts the form — so re-query the field rather than trust the stale
    // node. The secret clears in `finally` (after GET #2 resolves), so a cleared
    // field also proves the reload fired and the button is re-enabled.
    await waitFor(() => {
      const field = screen.getByLabelText(/restricted api key/i) as HTMLInputElement;
      expect(field.value).toBe('');
    });
    expect(accountGetCount()).toBe(2);
    // connect seeds default mappings server-side; the form MUST refetch them so
    // a later "Save Mappings" can't PUT stale nulls over the seed (Codex P1).
    expect(mappingGetCount()).toBe(2);
    expect(screen.getByRole('button', { name: /connect/i })).not.toBeDisabled();
  });

  it("surfaces the backend's verbatim 400 message on a rejected key", async () => {
    // An arbitrary sentinel proving verbatim pass-through of response.data.error;
    // it is NOT meant to track the exact backend copy (which lives in
    // stripe_connector/commands.py).
    const msg =
      'That looks like a SECRET key (sk_…), which grants write access. Nxentra is ' +
      'read-only — create a RESTRICTED key (rk_…) with Balance, Charges, Payouts and ' +
      'Disputes set to Read.';
    client.post.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 400, data: { error: msg } },
    });
    render(<StripeSettingsPage />);
    const input = (await screen.findByLabelText(/restricted api key/i)) as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'sk_live_oops' } });
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith({ title: msg, variant: 'destructive' });
    });
    // The error path must never reflect the submitted secret back to the user,
    // and the secret must not linger in the field after a failed attempt.
    const toastTitles = mockToast.mock.calls.map((c) => String(c[0]?.title ?? ''));
    expect(toastTitles.some((t) => t.includes('sk_live_oops'))).toBe(false);
    await waitFor(() => expect(input.value).toBe(''));
  });

  it('guards an empty key client-side (no network call)', async () => {
    render(<StripeSettingsPage />);
    await screen.findByLabelText(/restricted api key/i);
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith(
        expect.objectContaining({ variant: 'destructive' }),
      );
    });
    expect(client.post).not.toHaveBeenCalled();
  });
});

// ── Account Mappings display (connected) ─────────────────────────
// Regression for the "Not mapped" display bug: the connect seed maps
// STRIPE_CLEARING -> 11510 and EXPECTED_BANK_DEPOSIT -> 11610 (Stripe-specific
// GL accounts created AT connect). The accounts list that feeds the dropdown is
// fetched on mount and not yet refreshed, so those accounts are absent from the
// options. A controlled <select> whose value matches no <option> silently
// renders as "— Not mapped —" even though the role IS mapped server-side.
describe('Stripe Account Mappings display (connected)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Stale accounts cache: the shared accounts exist, but the Stripe-only
    // clearing / expected-bank-deposit accounts (ids 9001/9002) are NOT in the
    // list — exactly the post-connect state before the accounts query refetches.
    accountsMock.current = {
      data: [
        { id: 1, code: '41000', name: 'Sales Revenue', is_header: false, status: 'ACTIVE' },
        { id: 2, code: '11000', name: 'Cash and Bank', is_header: false, status: 'ACTIVE' },
      ],
      refetch: vi.fn(),
    };
    client.get.mockImplementation((url: string) => {
      if (url === '/stripe/account/')
        return Promise.resolve({
          data: {
            connected: true,
            status: 'ACTIVE',
            display_name: 'Acme',
            livemode: false,
            stripe_account_id: 'acct_1',
            created_at: '2026-06-01T00:00:00Z',
          },
        });
      if (url === '/stripe/account-mapping/')
        return Promise.resolve({
          data: [
            { role: 'SALES_REVENUE', account_id: 1, account_code: '41000', account_name: 'Sales Revenue' },
            { role: 'STRIPE_CLEARING', account_id: 9001, account_code: '11510', account_name: 'Stripe Clearing' },
            {
              role: 'EXPECTED_BANK_DEPOSIT',
              account_id: 9002,
              account_code: '11610',
              account_name: 'Expected Bank Deposit — Stripe',
            },
            { role: 'SALES_RETURNS', account_id: null, account_code: '', account_name: '' },
          ],
        });
      return Promise.resolve({ data: {} });
    });
  });

  afterEach(() => {
    accountsMock.current = { data: [], refetch: vi.fn() };
  });

  it('renders the seeded Stripe Clearing / Expected Bank Deposit mapping even when those accounts are absent from the (stale) accounts list', async () => {
    render(<StripeSettingsPage />);
    await screen.findByText('Account Mappings');

    // Mapping rows render in the order the API returns them:
    // [0] SALES_REVENUE, [1] STRIPE_CLEARING, [2] EXPECTED_BANK_DEPOSIT, [3] SALES_RETURNS.
    const selects = (await screen.findAllByRole('combobox')) as HTMLSelectElement[];
    expect(selects[1].value).toBe('9001'); // STRIPE_CLEARING shows its seeded account, not "" (Not mapped)
    expect(selects[2].value).toBe('9002'); // EXPECTED_BANK_DEPOSIT shows its seeded account

    // The seeded accounts are real, labeled options (the union of postable
    // accounts + each role's currently-mapped account), not the blank fallback.
    // Each select renders the shared option list, so the option appears once per
    // role row — assert it exists at all (getByRole would reject the duplicates).
    expect(screen.getAllByRole('option', { name: '11510 — Stripe Clearing' }).length).toBeGreaterThan(0);
    expect(
      screen.getAllByRole('option', { name: '11610 — Expected Bank Deposit — Stripe' }).length,
    ).toBeGreaterThan(0);

    // ...but the synthesized option is scoped to ITS OWN row: a non-postable
    // mapped account must not become selectable for another role (Codex P2). The
    // SALES_REVENUE row (index 0) offers neither 11510 nor 11610.
    expect(within(selects[0]).queryByRole('option', { name: '11510 — Stripe Clearing' })).toBeNull();
    expect(
      within(selects[0]).queryByRole('option', { name: '11610 — Expected Bank Deposit — Stripe' }),
    ).toBeNull();
  });

  it('labels the EXPECTED_BANK_DEPOSIT and SALES_RETURNS roles with friendly names (not raw role keys)', async () => {
    render(<StripeSettingsPage />);
    await screen.findByText('Account Mappings');

    expect(screen.getByText('Expected Bank Deposit')).toBeInTheDocument();
    expect(screen.getByText('Sales Returns / Failed Delivery')).toBeInTheDocument();
    // The raw role keys must not leak into the UI.
    expect(screen.queryByText('EXPECTED_BANK_DEPOSIT')).not.toBeInTheDocument();
    expect(screen.queryByText('SALES_RETURNS')).not.toBeInTheDocument();
  });

  it('refetches the accounts list after a successful connect so freshly-seeded accounts become selectable', async () => {
    // Start disconnected so the connect form renders.
    client.get.mockImplementation((url: string) => {
      if (url === '/stripe/account/') return Promise.resolve({ data: { connected: false } });
      if (url === '/stripe/account-mapping/') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: {} });
    });
    client.post.mockResolvedValueOnce({
      data: { connected: true, stripe_account_id: 'acct_1', status: 'ACTIVE', livemode: false, display_name: 'Acme' },
    });
    const refetch = accountsMock.current.refetch;

    render(<StripeSettingsPage />);
    const input = (await screen.findByLabelText(/restricted api key/i)) as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'rk_test_abc123' } });
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => expect(refetch).toHaveBeenCalled());
  });
});
