import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// Mock next-i18next
vi.mock('next-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback || key,
  }),
}));

// Mock Badge component
vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children, variant, className }: { children: React.ReactNode; variant?: string; className?: string }) => (
    <span data-testid="badge" data-variant={variant} className={className}>{children}</span>
  ),
}));

// Mock Dialog components
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children, open }: { children: React.ReactNode; open: boolean }) =>
    open ? <div data-testid="dialog">{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Mock Button
vi.mock('@/components/ui/button', () => ({
  Button: ({ children, onClick, disabled, variant }: any) => (
    <button onClick={onClick} disabled={disabled} data-variant={variant}>{children}</button>
  ),
}));

import { EmptyState } from '@/components/common/EmptyState';
import { StatusBadge } from '@/components/common/StatusBadge';
import { ConfirmDialog } from '@/components/common/ConfirmDialog';

// =============================================================================
// EmptyState Tests
// =============================================================================

describe('EmptyState', () => {
  it('renders with default icon and title', () => {
    render(<EmptyState />);
    expect(screen.getByText('messages.noData')).toBeInTheDocument();
  });

  it('renders custom title', () => {
    render(<EmptyState title="No customers yet" />);
    expect(screen.getByText('No customers yet')).toBeInTheDocument();
  });

  it('renders custom description', () => {
    render(<EmptyState description="Add your first customer to get started." />);
    expect(screen.getByText('Add your first customer to get started.')).toBeInTheDocument();
  });

  it('does not render description when not provided', () => {
    const { container } = render(<EmptyState title="Empty" />);
    expect(container.querySelectorAll('p').length).toBe(0);
  });

  it('renders custom action', () => {
    render(<EmptyState action={<button>Add Item</button>} />);
    expect(screen.getByText('Add Item')).toBeInTheDocument();
  });

  it('renders custom icon', () => {
    render(<EmptyState icon={<span data-testid="custom-icon">ICON</span>} />);
    expect(screen.getByTestId('custom-icon')).toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(<EmptyState className="my-custom-class" />);
    expect(container.firstChild).toHaveClass('my-custom-class');
  });
});

// =============================================================================
// StatusBadge Tests
// =============================================================================

describe('StatusBadge', () => {
  it('renders POSTED status with success variant', () => {
    render(<StatusBadge status="POSTED" />);
    const badge = screen.getByTestId('badge');
    expect(badge).toHaveAttribute('data-variant', 'success');
    expect(badge).toHaveTextContent('POSTED');
  });

  it('renders DRAFT status with warning variant', () => {
    render(<StatusBadge status="DRAFT" />);
    const badge = screen.getByTestId('badge');
    expect(badge).toHaveAttribute('data-variant', 'warning');
  });

  it('renders INCOMPLETE status with secondary variant', () => {
    render(<StatusBadge status="INCOMPLETE" />);
    const badge = screen.getByTestId('badge');
    expect(badge).toHaveAttribute('data-variant', 'secondary');
  });

  it('renders REVERSED status with info variant', () => {
    render(<StatusBadge status="REVERSED" />);
    const badge = screen.getByTestId('badge');
    expect(badge).toHaveAttribute('data-variant', 'info');
  });

  it('renders ACTIVE account status with success variant', () => {
    render(<StatusBadge status="ACTIVE" />);
    const badge = screen.getByTestId('badge');
    expect(badge).toHaveAttribute('data-variant', 'success');
  });

  it('renders LOCKED account status with warning variant', () => {
    render(<StatusBadge status="LOCKED" />);
    const badge = screen.getByTestId('badge');
    expect(badge).toHaveAttribute('data-variant', 'warning');
  });

  it('applies custom className', () => {
    render(<StatusBadge status="POSTED" className="extra-class" />);
    const badge = screen.getByTestId('badge');
    expect(badge).toHaveClass('extra-class');
  });
});

// =============================================================================
// ConfirmDialog Tests
// =============================================================================

describe('ConfirmDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    title: 'Delete Item',
    description: 'Are you sure?',
    onConfirm: vi.fn(),
  };

  it('renders when open', () => {
    render(<ConfirmDialog {...defaultProps} />);
    expect(screen.getByText('Delete Item')).toBeInTheDocument();
    expect(screen.getByText('Are you sure?')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    render(<ConfirmDialog {...defaultProps} open={false} />);
    expect(screen.queryByText('Delete Item')).not.toBeInTheDocument();
  });

  it('calls onConfirm when confirm button clicked', () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog {...defaultProps} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByText('actions.confirm'));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('calls onOpenChange(false) when cancel clicked', () => {
    const onOpenChange = vi.fn();
    render(<ConfirmDialog {...defaultProps} onOpenChange={onOpenChange} />);
    fireEvent.click(screen.getByText('actions.cancel'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('renders custom confirm/cancel labels', () => {
    render(<ConfirmDialog {...defaultProps} confirmLabel="Yes, delete" cancelLabel="No, keep" />);
    expect(screen.getByText('Yes, delete')).toBeInTheDocument();
    expect(screen.getByText('No, keep')).toBeInTheDocument();
  });

  it('shows loading state', () => {
    render(<ConfirmDialog {...defaultProps} isLoading={true} />);
    expect(screen.getByText('actions.loading')).toBeInTheDocument();
    expect(screen.getByText('actions.loading')).toBeDisabled();
  });

  it('renders destructive variant', () => {
    render(<ConfirmDialog {...defaultProps} variant="destructive" />);
    const confirmBtn = screen.getByText('actions.confirm');
    expect(confirmBtn).toHaveAttribute('data-variant', 'destructive');
  });

  it('renders children content', () => {
    render(
      <ConfirmDialog {...defaultProps}>
        <input data-testid="extra-input" />
      </ConfirmDialog>
    );
    expect(screen.getByTestId('extra-input')).toBeInTheDocument();
  });
});
