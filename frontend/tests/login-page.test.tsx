import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Mock next/router
const mockPush = vi.fn();
vi.mock('next/router', () => ({
  useRouter: () => ({ push: mockPush, query: {} }),
}));

// Mock next/link
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

// Mock AuthLayout
vi.mock('@/components/AuthLayout', () => ({
  AuthLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Mock FormField
vi.mock('@/components/FormField', () => ({
  InputField: ({
    id,
    label,
    type,
    value,
    onChange,
  }: {
    id: string;
    label: string;
    type?: string;
    value: string;
    onChange: (val: string) => void;
  }) => (
    <div>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={id}
      />
    </div>
  ),
}));

// Mock API
const mockLogin = vi.fn();
vi.mock('@/lib/api', () => ({
  login: (...args: unknown[]) => mockLogin(...args),
}));

// Mock auth-storage
vi.mock('@/lib/auth-storage', () => ({
  storeTokens: vi.fn(),
}));

import LoginPage from '@/pages/login';

describe('LoginPage', () => {
  beforeEach(() => {
    mockLogin.mockReset();
    mockPush.mockReset();
  });

  it('renders login form with email and password fields', () => {
    render(<LoginPage />);
    expect(screen.getByText('Sign in')).toBeInTheDocument();
    expect(screen.getByTestId('email')).toBeInTheDocument();
    expect(screen.getByTestId('password')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /login/i })).toBeInTheDocument();
  });

  it('shows register link', () => {
    render(<LoginPage />);
    expect(screen.getByText('Get started')).toHaveAttribute('href', '/register');
  });

  it('full-page redirects to dashboard on direct-token login (F2 single-company)', async () => {
    // F2: a single-company user gets tokens on the first login. The success
    // path uses a full-page redirect (window.location.href), NOT router.push,
    // so AuthContext re-initializes with the new tokens (no login loop).
    const origLocation = window.location;
    // @ts-expect-error — jsdom lets us swap location for a plain object
    delete window.location;
    // @ts-expect-error
    window.location = { href: '' };

    try {
      mockLogin.mockResolvedValue({ access: 'jwt-token', refresh: 'refresh-token' });
      render(<LoginPage />);

      fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
      fireEvent.change(screen.getByTestId('password'), { target: { value: 'password123' } });
      fireEvent.click(screen.getByRole('button', { name: /login/i }));

      await waitFor(() => {
        expect(mockLogin).toHaveBeenCalledWith('user@test.com', 'password123');
        expect(window.location.href).toBe('/dashboard');
      });
      expect(mockPush).not.toHaveBeenCalled();
    } finally {
      window.location = origLocation;
    }
  });

  it('redirects to company selection when choose_company response', async () => {
    sessionStorage.clear();
    mockLogin.mockResolvedValue({
      detail: 'choose_company',
      pending_login_token: 'signed.token.value',
      companies: [{ id: 1, name: 'Acme', role: 'OWNER' }],
    });
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'pass' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith('/select-company?from=login');
    });
    // Token + companies are stored; password and email are NOT.
    expect(sessionStorage.getItem('pendingLoginToken')).toBe('signed.token.value');
    expect(sessionStorage.getItem('pendingCompanies')).toBe(
      JSON.stringify([{ id: 1, name: 'Acme', role: 'OWNER' }])
    );
  });

  // A87 regression: the browser must NEVER store the user's password or email
  // in sessionStorage during the company-selection step. The backend gives us a
  // short-lived signed token instead.
  it('does not store password or email in sessionStorage on choose_company (A87)', async () => {
    sessionStorage.clear();
    mockLogin.mockResolvedValue({
      detail: 'choose_company',
      pending_login_token: 'signed.token.value',
      companies: [{ id: 1, name: 'Acme', role: 'OWNER' }],
    });
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'supersecret' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith('/select-company?from=login');
    });

    expect(sessionStorage.getItem('pendingPassword')).toBeNull();
    expect(sessionStorage.getItem('pendingEmail')).toBeNull();
    // And as a paranoia check: no sessionStorage key may contain the password text.
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i)!;
      const value = sessionStorage.getItem(key) ?? '';
      expect(value).not.toContain('supersecret');
    }
  });

  it('stays on login (does not redirect) if choose_company response is missing the token', async () => {
    sessionStorage.clear();
    mockLogin.mockResolvedValue({
      detail: 'choose_company',
      // pending_login_token deliberately missing
      companies: [{ id: 1, name: 'Acme', role: 'OWNER' }],
    });
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'pass' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    // Wait a tick for the async submit to settle.
    await waitFor(() => expect(mockLogin).toHaveBeenCalled());

    expect(mockPush).not.toHaveBeenCalledWith('/select-company?from=login');
    expect(sessionStorage.getItem('pendingLoginToken')).toBeNull();
  });

  it('redirects to verify-email when email not verified', async () => {
    mockLogin.mockRejectedValue({
      response: { data: { detail: 'email_not_verified' } },
    });
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'unverified@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'pass' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith('/verify-email?email=unverified%40test.com');
    });
  });

  it('redirects to pending-approval when pending', async () => {
    mockLogin.mockRejectedValue({
      response: { data: { detail: 'pending_approval' } },
    });
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'pending@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'pass' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith('/pending-approval');
    });
  });

  it('shows error message for no company access', async () => {
    mockLogin.mockRejectedValue({
      response: { data: { detail: 'no_company_access' } },
    });
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'pass' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(screen.getByText(/don't have access to any companies/i)).toBeInTheDocument();
    });
  });

  it('shows generic error message for invalid credentials', async () => {
    mockLogin.mockRejectedValue({
      response: { data: { detail: 'No active account found with the given credentials' } },
    });
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(screen.getByText(/invalid credentials/i)).toBeInTheDocument();
    });
  });

  it('shows "Authenticating..." while submitting', async () => {
    mockLogin.mockReturnValue(new Promise(() => {})); // Never resolves
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'pass' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(screen.getByText('Authenticating...')).toBeInTheDocument();
    });
  });

  it('disables submit button while submitting', async () => {
    mockLogin.mockReturnValue(new Promise(() => {})); // Never resolves
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'pass' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(screen.getByText('Authenticating...')).toBeDisabled();
    });
  });
});
