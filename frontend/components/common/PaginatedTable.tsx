import React, { ReactNode } from "react";
import { useTranslation } from "next-i18next";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface ColumnDef<T> {
  key: string;
  label: string;
  render: (item: T) => ReactNode;
  sortable?: boolean;
  className?: string;
}

interface PaginatedTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  keyExtractor: (item: T) => string | number;
  // Pagination
  page: number;
  pageSize: number;
  totalCount: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  // Sorting
  ordering?: string;
  onOrderingChange?: (ordering: string) => void;
  // Row interaction
  onRowClick?: (item: T) => void;
  // State
  isLoading?: boolean;
  emptyState?: ReactNode;
}

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

export function PaginatedTable<T>({
  data,
  columns,
  keyExtractor,
  page,
  pageSize,
  totalCount,
  totalPages,
  onPageChange,
  onPageSizeChange,
  ordering,
  onOrderingChange,
  onRowClick,
  isLoading,
  emptyState,
}: PaginatedTableProps<T>) {
  const { t } = useTranslation("common");

  const handleSort = (key: string) => {
    if (!onOrderingChange) return;
    if (ordering === key) {
      onOrderingChange(`-${key}`);
    } else if (ordering === `-${key}`) {
      onOrderingChange(key);
    } else {
      onOrderingChange(key);
    }
  };

  const renderSortIcon = (key: string) => {
    if (!ordering) return <ArrowUpDown className="ms-1 h-3 w-3 text-muted-foreground/50" />;
    if (ordering === key) return <ArrowUp className="ms-1 h-3 w-3" />;
    if (ordering === `-${key}`) return <ArrowDown className="ms-1 h-3 w-3" />;
    return <ArrowUpDown className="ms-1 h-3 w-3 text-muted-foreground/50" />;
  };

  if (!isLoading && data.length === 0 && emptyState) {
    return <>{emptyState}</>;
  }

  const from = totalCount === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, totalCount);

  return (
    <div className="space-y-4">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((col) => (
              <TableHead
                key={col.key}
                className={`${col.className || ""} ${col.sortable ? "cursor-pointer select-none hover:text-foreground" : ""}`}
                onClick={col.sortable ? () => handleSort(col.key) : undefined}
              >
                <span className="inline-flex items-center">
                  {col.label}
                  {col.sortable && renderSortIcon(col.key)}
                </span>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading ? (
            Array.from({ length: Math.min(pageSize, 5) }).map((_, i) => (
              <TableRow key={`skeleton-${i}`}>
                {columns.map((col) => (
                  <TableCell key={col.key}>
                    <div className="h-4 bg-muted animate-pulse rounded w-3/4" />
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            data.map((item) => (
              <TableRow
                key={keyExtractor(item)}
                className={onRowClick ? "cursor-pointer" : ""}
                onClick={onRowClick ? () => onRowClick(item) : undefined}
              >
                {columns.map((col) => (
                  <TableCell key={col.key} className={col.className}>
                    {col.render(item)}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      {/* Pagination Controls */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 px-2">
        <div className="text-sm text-muted-foreground">
          {totalCount > 0
            ? t("table.showing", "Showing {{from}}-{{to}} of {{total}}", { from, to, total: totalCount })
            : t("table.noResults", "No results")}
        </div>

        <div className="flex items-center gap-4">
          {/* Page size selector */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">{t("table.rowsPerPage", "Rows")}</span>
            <Select
              value={String(pageSize)}
              onValueChange={(val) => {
                onPageSizeChange(Number(val));
                onPageChange(1);
              }}
            >
              <SelectTrigger className="h-8 w-16">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Page indicator */}
          <span className="text-sm text-muted-foreground">
            {t("table.pageOf", "Page {{page}} of {{total}}", { page, total: totalPages })}
          </span>

          {/* Navigation buttons */}
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => onPageChange(1)}
              disabled={page <= 1}
            >
              <ChevronsLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => onPageChange(page - 1)}
              disabled={page <= 1}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => onPageChange(page + 1)}
              disabled={page >= totalPages}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => onPageChange(totalPages)}
              disabled={page >= totalPages}
            >
              <ChevronsRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
