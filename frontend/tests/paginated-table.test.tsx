import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// Mock next-i18next
vi.mock('next-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string, opts?: Record<string, unknown>) => {
      if (opts && fallback) {
        // Simple interpolation for "Showing {{from}}-{{to}} of {{total}}"
        let result = fallback;
        Object.entries(opts).forEach(([k, v]) => {
          result = result.replace(`{{${k}}}`, String(v));
        });
        return result;
      }
      return fallback || key;
    },
  }),
}));

import { PaginatedTable, ColumnDef } from '@/components/common/PaginatedTable';

interface TestItem {
  id: number;
  name: string;
  amount: number;
}

const testColumns: ColumnDef<TestItem>[] = [
  { key: 'name', label: 'Name', sortable: true, render: (item) => item.name },
  { key: 'amount', label: 'Amount', sortable: true, className: 'text-end', render: (item) => `$${item.amount}` },
];

const testData: TestItem[] = [
  { id: 1, name: 'Alpha', amount: 100 },
  { id: 2, name: 'Beta', amount: 200 },
  { id: 3, name: 'Gamma', amount: 300 },
];

const defaultProps = {
  data: testData,
  columns: testColumns,
  keyExtractor: (item: TestItem) => item.id,
  page: 1,
  pageSize: 25,
  totalCount: 3,
  totalPages: 1,
  onPageChange: vi.fn(),
  onPageSizeChange: vi.fn(),
};

describe('PaginatedTable', () => {
  it('renders column headers', () => {
    render(<PaginatedTable {...defaultProps} />);
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('Amount')).toBeInTheDocument();
  });

  it('renders data rows', () => {
    render(<PaginatedTable {...defaultProps} />);
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
    expect(screen.getByText('Gamma')).toBeInTheDocument();
    expect(screen.getByText('$100')).toBeInTheDocument();
    expect(screen.getByText('$200')).toBeInTheDocument();
  });

  it('shows "Showing 1-3 of 3" indicator', () => {
    render(<PaginatedTable {...defaultProps} />);
    expect(screen.getByText('Showing 1-3 of 3')).toBeInTheDocument();
  });

  it('shows "No results" when totalCount is 0', () => {
    render(<PaginatedTable {...defaultProps} data={[]} totalCount={0} />);
    expect(screen.getByText('No results')).toBeInTheDocument();
  });

  it('renders empty state when data is empty and emptyState provided', () => {
    render(
      <PaginatedTable
        {...defaultProps}
        data={[]}
        totalCount={0}
        emptyState={<div>No items found</div>}
      />
    );
    expect(screen.getByText('No items found')).toBeInTheDocument();
  });

  it('renders skeleton rows when loading', () => {
    const { container } = render(
      <PaginatedTable {...defaultProps} data={[]} isLoading={true} />
    );
    // Should render skeleton animation divs
    const skeletons = container.querySelectorAll('.animate-pulse');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('disables prev/first buttons on page 1', () => {
    render(<PaginatedTable {...defaultProps} page={1} totalPages={3} />);
    const buttons = screen.getAllByRole('button');
    // First two nav buttons (first page, prev page) should be disabled
    const firstBtn = buttons.find(b => b.querySelector('.lucide-chevrons-left'));
    const prevBtn = buttons.find(b => b.querySelector('.lucide-chevron-left'));
    expect(firstBtn).toBeDisabled();
    expect(prevBtn).toBeDisabled();
  });

  it('disables next/last buttons on last page', () => {
    render(<PaginatedTable {...defaultProps} page={3} totalPages={3} />);
    const buttons = screen.getAllByRole('button');
    const nextBtn = buttons.find(b => b.querySelector('.lucide-chevron-right'));
    const lastBtn = buttons.find(b => b.querySelector('.lucide-chevrons-right'));
    expect(nextBtn).toBeDisabled();
    expect(lastBtn).toBeDisabled();
  });

  it('calls onPageChange when next button clicked', () => {
    const onPageChange = vi.fn();
    render(
      <PaginatedTable {...defaultProps} page={1} totalPages={3} onPageChange={onPageChange} />
    );
    const buttons = screen.getAllByRole('button');
    const nextBtn = buttons.find(b => b.querySelector('.lucide-chevron-right'));
    fireEvent.click(nextBtn!);
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it('calls onPageChange(1) when first page button clicked', () => {
    const onPageChange = vi.fn();
    render(
      <PaginatedTable {...defaultProps} page={2} totalPages={3} onPageChange={onPageChange} />
    );
    const buttons = screen.getAllByRole('button');
    const firstBtn = buttons.find(b => b.querySelector('.lucide-chevrons-left'));
    fireEvent.click(firstBtn!);
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it('calls onRowClick when a row is clicked', () => {
    const onRowClick = vi.fn();
    render(<PaginatedTable {...defaultProps} onRowClick={onRowClick} />);
    fireEvent.click(screen.getByText('Alpha'));
    expect(onRowClick).toHaveBeenCalledWith(testData[0]);
  });

  it('toggles sort ordering when sortable header clicked', () => {
    const onOrderingChange = vi.fn();
    render(
      <PaginatedTable {...defaultProps} ordering="name" onOrderingChange={onOrderingChange} />
    );
    // Click "Name" header — should toggle to "-name"
    fireEvent.click(screen.getByText('Name'));
    expect(onOrderingChange).toHaveBeenCalledWith('-name');
  });

  it('sets ascending order on first click of unsorted column', () => {
    const onOrderingChange = vi.fn();
    render(
      <PaginatedTable {...defaultProps} ordering="name" onOrderingChange={onOrderingChange} />
    );
    // Click "Amount" header — should set to "amount" (ascending)
    fireEvent.click(screen.getByText('Amount'));
    expect(onOrderingChange).toHaveBeenCalledWith('amount');
  });

  it('shows page indicator', () => {
    render(<PaginatedTable {...defaultProps} page={2} totalPages={5} />);
    expect(screen.getByText('Page 2 of 5')).toBeInTheDocument();
  });

  it('shows correct range for middle page', () => {
    render(
      <PaginatedTable {...defaultProps} page={2} pageSize={10} totalCount={25} totalPages={3} />
    );
    expect(screen.getByText('Showing 11-20 of 25')).toBeInTheDocument();
  });
});
