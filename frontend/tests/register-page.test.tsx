import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockPush = vi.fn();
vi.mock('next/router', () => ({
  useRouter: () => ({ push: mockPush, query: {} }),
}));

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock('@/components/AuthLayout', () => ({
  AuthLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/FormField', () => ({
  InputField: ({ id, label, value, onChange, error, type, placeholder }: any) => (
    <div>
      <label htmlFor={id}>{label}</label>
      <input id={id} type={type} value={value} onChange={(e: any) => onChange(e.target.value)} placeholder={placeholder} data-testid={id} />
      {error && <span data-testid={`${id}-error`}>{error}</span>}
    </div>
  ),
  PasswordField: ({ id, label, value, onChange, error, hint }: any) => (
    <div>
      <label htmlFor={id}>{label}</label>
      <input id={id} type="password" value={value} onChange={(e: any) => onChange(e.target.value)} data-testid={id} />
      {error && <span data-testid={`${id}-error`}>{error}</span>}
      {hint && <span data-testid={`${id}-hint`}>{hint}</span>}
    </div>
  ),
  SelectField: ({ id, label, value, onChange, children }: any) => (
    <div>
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e: any) => onChange(e.target.value)} data-testid={id}>{children}</select>
    </div>
  ),
}));

vi.mock('@/lib/constants', () => ({
  currencyOptions: ['USD', 'EUR', 'EGP'],
  languageOptions: [{ value: 'en', label: 'English' }, { value: 'ar', label: 'Arabic' }],
}));

const mockRegister = vi.fn();
vi.mock('@/lib/api', () => ({
  register: (...args: unknown[]) => mockRegister(...args),
}));

import RegisterPage from '@/pages/register';

describe('RegisterPage', () => {
  beforeEach(() => {
    mockRegister.mockReset();
    mockPush.mockReset();
  });

  it('renders registration form', () => {
    render(<RegisterPage />);
    expect(screen.getByText('Get started')).toBeInTheDocument();
    expect(screen.getByTestId('email')).toBeInTheDocument();
    expect(screen.getByTestId('name')).toBeInTheDocument();
    expect(screen.getByTestId('password')).toBeInTheDocument();
    expect(screen.getByTestId('company_name')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /launch workspace/i })).toBeInTheDocument();
  });

  it('shows login link', () => {
    render(<RegisterPage />);
    expect(screen.getByText('Sign in')).toHaveAttribute('href', '/login');
  });

  it('validates required fields', async () => {
    render(<RegisterPage />);
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByTestId('email-error')).toHaveTextContent('Email is required');
      expect(screen.getByTestId('name-error')).toHaveTextContent('Name is required');
      expect(screen.getByTestId('password-error')).toHaveTextContent('Password is required');
      expect(screen.getByTestId('confirm_password-error')).toHaveTextContent('Please confirm your password');
      expect(screen.getByTestId('company_name-error')).toHaveTextContent('Company database name is required');
    });

    expect(mockRegister).not.toHaveBeenCalled();
  });

  it.each([
    ['too short', 'Ab1!'],
    ['no uppercase letter', 'password123!'],
    ['no number', 'Password!!!'],
    ['no special character', 'Password123'],
  ])('rejects a password with %s', async (_case, badPassword) => {
    render(<RegisterPage />);
    fireEvent.change(screen.getByTestId('email'), { target: { value: 'test@test.com' } });
    fireEvent.change(screen.getByTestId('name'), { target: { value: 'Test User' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: badPassword } });
    fireEvent.change(screen.getByTestId('confirm_password'), { target: { value: badPassword } });
    fireEvent.change(screen.getByTestId('company_name'), { target: { value: 'acme' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByTestId('password-error')).toHaveTextContent(
        'Password does not meet all the requirements below'
      );
    });
    expect(mockRegister).not.toHaveBeenCalled();
  });

  it('requires password confirmation', async () => {
    render(<RegisterPage />);
    fireEvent.change(screen.getByTestId('email'), { target: { value: 'test@test.com' } });
    fireEvent.change(screen.getByTestId('name'), { target: { value: 'Test User' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('company_name'), { target: { value: 'acme' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByTestId('confirm_password-error')).toHaveTextContent('Please confirm your password');
    });
    expect(mockRegister).not.toHaveBeenCalled();
  });

  it('rejects mismatched password confirmation', async () => {
    render(<RegisterPage />);
    fireEvent.change(screen.getByTestId('email'), { target: { value: 'test@test.com' } });
    fireEvent.change(screen.getByTestId('name'), { target: { value: 'Test User' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('confirm_password'), { target: { value: 'Password124!' } });
    fireEvent.change(screen.getByTestId('company_name'), { target: { value: 'acme' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByTestId('confirm_password-error')).toHaveTextContent('Passwords do not match');
    });
    expect(mockRegister).not.toHaveBeenCalled();
  });

  it('shows the four-rule checklist and ticks each rule live', () => {
    render(<RegisterPage />);
    const hint = () => screen.getByTestId('password-hint');
    expect(hint()).toHaveTextContent('At least 8 characters');
    expect(hint()).toHaveTextContent('One uppercase letter (A–Z)');
    expect(hint()).toHaveTextContent('One number (0–9)');
    expect(hint()).toHaveTextContent('One special character (e.g. !@#$%)');
    expect(hint()).not.toHaveTextContent('✓');

    // Partial: length + number met, uppercase + special not
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'password123' } });
    expect(hint()).toHaveTextContent('✓ At least 8 characters');
    expect(hint()).toHaveTextContent('✓ One number (0–9)');
    expect(hint()).toHaveTextContent('• One uppercase letter (A–Z)');
    expect(hint()).toHaveTextContent('• One special character (e.g. !@#$%)');

    // All four met
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    expect(hint()).not.toHaveTextContent('•');
  });

  it('routes a backend password rejection to the password field, not email', async () => {
    mockRegister.mockRejectedValue({
      response: { data: { detail: 'Password must include at least one uppercase letter.' } },
    });
    render(<RegisterPage />);
    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('name'), { target: { value: 'User' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('confirm_password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('company_name'), { target: { value: 'acme' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByTestId('password-error')).toHaveTextContent(
        'Password must include at least one uppercase letter.'
      );
    });
    expect(screen.queryByTestId('email-error')).not.toBeInTheDocument();
  });

  it('clears a stale password error (and mismatch error) once the user edits again', async () => {
    render(<RegisterPage />);
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'short' } });
    fireEvent.change(screen.getByTestId('confirm_password'), { target: { value: 'different' } });
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByTestId('password-error')).toHaveTextContent(
        'Password does not meet all the requirements below'
      );
      expect(screen.getByTestId('confirm_password-error')).toHaveTextContent('Passwords do not match');
    });

    // Editing the password clears its own error AND the stale mismatch
    // error, since the match depends on both fields.
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    expect(screen.queryByTestId('password-error')).not.toBeInTheDocument();
    expect(screen.getByTestId('password-hint')).toHaveTextContent('✓ At least 8 characters');
    expect(screen.queryByTestId('confirm_password-error')).not.toBeInTheDocument();
  });

  it('validates company name has no spaces', async () => {
    render(<RegisterPage />);
    fireEvent.change(screen.getByTestId('email'), { target: { value: 'test@test.com' } });
    fireEvent.change(screen.getByTestId('name'), { target: { value: 'Test User' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('company_name'), { target: { value: 'my company' } });
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByTestId('company_name-error')).toHaveTextContent('Use a single word with no spaces');
    });
  });

  it('validates company name max 10 characters', async () => {
    render(<RegisterPage />);
    fireEvent.change(screen.getByTestId('email'), { target: { value: 'test@test.com' } });
    fireEvent.change(screen.getByTestId('name'), { target: { value: 'Test User' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('company_name'), { target: { value: 'verylongname' } });
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByTestId('company_name-error')).toHaveTextContent('Maximum 10 characters');
    });
  });

  it('submits valid form and redirects to verify-email', async () => {
    mockRegister.mockResolvedValue({});
    render(<RegisterPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('name'), { target: { value: 'Test User' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('confirm_password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('company_name'), { target: { value: 'acme' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      // Exact-match payload: also proves confirm_password is never sent to the API
      expect(mockRegister).toHaveBeenCalledWith({
        email: 'user@test.com',
        name: 'Test User',
        phone: '',
        password: 'Password123!',
        company_name: 'acme',
        currency: 'USD',
        language: 'en',
        tos_accepted: true,
      });
      expect(mockPush).toHaveBeenCalledWith('/verify-email?email=user%40test.com&sent=true');
    });
  });

  it('shows error on registration failure', async () => {
    mockRegister.mockRejectedValue({
      response: { data: { detail: 'Email already registered' } },
    });
    render(<RegisterPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'existing@test.com' } });
    fireEvent.change(screen.getByTestId('name'), { target: { value: 'User' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('confirm_password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('company_name'), { target: { value: 'acme' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByTestId('email-error')).toHaveTextContent('Email already registered');
    });
  });

  it('shows Submitting... while in progress', async () => {
    mockRegister.mockReturnValue(new Promise(() => {}));
    render(<RegisterPage />);

    fireEvent.change(screen.getByTestId('email'), { target: { value: 'user@test.com' } });
    fireEvent.change(screen.getByTestId('name'), { target: { value: 'User' } });
    fireEvent.change(screen.getByTestId('password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('confirm_password'), { target: { value: 'Password123!' } });
    fireEvent.change(screen.getByTestId('company_name'), { target: { value: 'acme' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /launch workspace/i }));

    await waitFor(() => {
      expect(screen.getByText('Submitting...')).toBeInTheDocument();
    });
  });
});
