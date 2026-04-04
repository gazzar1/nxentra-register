/**
 * Journal Entries List Page — Component Tests
 *
 * Tests the core accounting list page: filtering, tab switching,
 * pagination resets, and data rendering.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ── Mocks ───���────────────────────────────────────────────────────

vi.mock('next-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback || key.split('.').pop() || key,
  }),
}));

vi.mock('next-i18next/serverSideTranslations', () => ({
  serverSideTranslations: vi.fn().mockResolvedValue({}),
}));

const mockPush = vi.fn();
vi.mock('next/router', () => ({
  useRouter: () => ({
    push: mockPush,
    pathname: '/accounting/journal-entries',
    query: {},
    locale: 'en',
  }),
}));

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 1, name: 'Test', email: 'test@test.com' },
    company: { id: 1, name: 'Test Co', default_currency: 'USD' },
  }),
}));

vi.mock('@/hooks/useCompanyFormat', () => ({
  useCompanyFormat: () => ({
    formatCurrency: (v: string) => `$${v}`,
    formatAmount: (v: string) => v,
    formatDate: (v: string) => v,
  }),
}));

const mockJournalData = {
  results: [
    {
      id: 1,
      public_id: 'abc-123',
      entry_number: 'JE-2026-0001',
      date: '2026-03-15',
      memo: 'Sales revenue Q1',
      status: 'POSTED',
      total_debit: '5000.00',
      total_credit: '5000.00',
      currency: 'USD',
      line_count: 2,
      created_by_name: 'Test User',
    },
    {
      id: 2,
      public_id: 'def-456',
      entry_number: 'JE-2026-0002',
      date: '2026-03-20',
      memo: 'Office rent',
      status: 'POSTED',
      total_debit: '3500.00',
      total_credit: '3500.00',
      currency: 'USD',
      line_count: 2,
      created_by_name: 'Test User',
    },
  ],
  count: 2,
  total_pages: 1,
};

const mockUseQuery = vi.fn().mockReturnValue({
  data: mockJournalData,
  isLoading: false,
});

vi.mock('@/queries/useJournalEntries', () => ({
  usePaginatedJournalEntries: (...args: unknown[]) => mockUseQuery(...args),
}));

vi.mock('@/services/export.service', () => ({
  exportService: {
    exportJournalEntries: vi.fn().mockResolvedValue(undefined),
  },
  ExportFormat: { CSV: 'csv', XLSX: 'xlsx' },
}));

vi.mock('@/components/ui/toaster', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

vi.mock('@/components/layout', () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import JournalEntriesPage from '@/pages/accounting/journal-entries/index';

// ── Tests ────────────────────────────────────────────────────────

describe('JournalEntriesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseQuery.mockReturnValue({ data: mockJournalData, isLoading: false });
  });

  it('renders journal entry numbers and memos', () => {
    render(<JournalEntriesPage />);
    expect(screen.getByText('JE-2026-0001')).toBeInTheDocument();
    expect(screen.getByText('JE-2026-0002')).toBeInTheDocument();
    expect(screen.getByText('Sales revenue Q1')).toBeInTheDocument();
    expect(screen.getByText('Office rent')).toBeInTheDocument();
  });

  it('passes POSTED status filter by default', () => {
    render(<JournalEntriesPage />);
    expect(mockUseQuery).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'POSTED' })
    );
  });

  it('resets page to 1 when search changes', async () => {
    render(<JournalEntriesPage />);
    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'revenue' } });
    await waitFor(() => {
      expect(mockUseQuery).toHaveBeenCalledWith(
        expect.objectContaining({ search: 'revenue', page: 1 })
      );
    });
  });

  it('shows empty table when no results', () => {
    mockUseQuery.mockReturnValue({
      data: { results: [], count: 0, total_pages: 1 },
      isLoading: false,
    });
    render(<JournalEntriesPage />);
    expect(screen.queryByText('JE-2026-0001')).not.toBeInTheDocument();
  });

  it('has link to create new entry', () => {
    render(<JournalEntriesPage />);
    const link = screen.getByRole('link', { name: /createEntry/i });
    expect(link).toHaveAttribute('href', '/accounting/journal-entries/new');
  });

  it('renders Posted and Drafts tabs', () => {
    render(<JournalEntriesPage />);
    expect(screen.getByRole('tab', { name: /posted/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /drafts/i })).toBeInTheDocument();
  });

  it('renders formatted currency amounts', () => {
    render(<JournalEntriesPage />);
    // formatCurrency mock prefixes $
    expect(screen.getAllByText('$5000.00').length).toBeGreaterThan(0);
    expect(screen.getAllByText('$3500.00').length).toBeGreaterThan(0);
  });
});
