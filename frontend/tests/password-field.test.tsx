import React, { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { PasswordField } from '@/components/FormField';

function ControlledPasswordField(props: { error?: string; hint?: React.ReactNode }) {
  const [value, setValue] = useState('');
  return (
    <PasswordField
      id="password"
      label="Password"
      value={value}
      onChange={setValue}
      {...props}
    />
  );
}

describe('PasswordField', () => {
  it('renders a masked input by default', () => {
    render(<ControlledPasswordField />);
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
    expect(screen.getByRole('button', { name: 'Show password' })).toBeInTheDocument();
  });

  it('toggles visibility with the eye button', () => {
    render(<ControlledPasswordField />);
    const input = screen.getByLabelText('Password');

    fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
    expect(input).toHaveAttribute('type', 'text');
    expect(screen.getByRole('button', { name: 'Hide password' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Hide password' }));
    expect(input).toHaveAttribute('type', 'password');
  });

  it('does not submit the surrounding form when toggling', () => {
    const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <ControlledPasswordField />
      </form>
    );
    fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('shows the hint when there is no error', () => {
    render(<ControlledPasswordField hint="At least 8 characters" />);
    expect(screen.getByText('At least 8 characters')).toBeInTheDocument();
  });

  it('shows the error and keeps the hint visible (the checklist explains the error)', () => {
    render(<ControlledPasswordField hint="At least 8 characters" error="Passwords do not match" />);
    expect(screen.getByText('Passwords do not match')).toBeInTheDocument();
    expect(screen.getByText('At least 8 characters')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toHaveAttribute(
      'aria-describedby',
      'password-error password-hint'
    );
  });

  it('propagates typed input', () => {
    render(<ControlledPasswordField />);
    const input = screen.getByLabelText('Password');
    fireEvent.change(input, { target: { value: 'secret-value' } });
    expect(input).toHaveValue('secret-value');
  });
});
