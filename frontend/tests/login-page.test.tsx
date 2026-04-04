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

  it('submits form and redirects to dashboard on successful login', async () => {
    mockLogin.mockResolvedValue({ access: 'jwt-token', refresh: 'refresh-token' });
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith('user@test.com', 'password123');
      expect(mockPush).toHaveBeenCalledWith('/dashboard');
    });
  });

  it('redirects to company selection when choose_company response', async () => {
    mockLogin.mockResolvedValue({
      detail: 'choose_company',
      companies: [{ id: 1, name: 'Acme', role: 'OWNER' }],
    });
    render(<LoginPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'pass' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith('/select-company?from=login');
    });
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
