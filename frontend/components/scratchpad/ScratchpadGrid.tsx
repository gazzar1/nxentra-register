import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  createColumnHelper,
  SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRouter } from "next/router";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  GripVertical,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type {
  ScratchpadRow,
  ScratchpadRowUpdatePayload,
  DimensionSchema,
  ValidationError,
} from "@/types/scratchpad";
import { SCRATCHPAD_STATUS_COLORS, SCRATCHPAD_STATUS_LABELS } from "@/types/scratchpad";
import type { Account } from "@/types/account";

interface ScratchpadGridProps {
  rows: ScratchpadRow[];
  accounts: Account[];
  dimensionSchema?: DimensionSchema;
  onRowUpdate: (publicId: string, data: ScratchpadRowUpdatePayload) => void;
  onRowDelete: (publicId: string) => void;
  selectedRows: string[];
  onSelectionChange: (selectedIds: string[]) => void;
  isLoading?: boolean;
}

const columnHelper = createColumnHelper<ScratchpadRow>();

export function ScratchpadGrid({
  rows,
  accounts,
  dimensionSchema,
  onRowUpdate,
  onRowDelete,
  selectedRows,
  onSelectionChange,
  isLoading = false,
}: ScratchpadGridProps) {
  const router = useRouter();
  const isRTL = router.locale === "ar";
  const parentRef = useRef<HTMLDivElement>(null);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [editingCell, setEditingCell] = useState<{
    rowId: string;
    columnId: string;
  } | null>(null);

  // Postable accounts only
  const postableAccounts = useMemo(
    () => accounts.filter((a) => a.is_postable),
    [accounts]
  );

  // Row selection helpers
  const isRowSelected = useCallback(
    (publicId: string) => selectedRows.includes(publicId),
    [selectedRows]
  );

  const toggleRowSelection = useCallback(
    (publicId: string) => {
      if (selectedRows.includes(publicId)) {
        onSelectionChange(selectedRows.filter((id) => id !== publicId));
      } else {
        onSelectionChange([...selectedRows, publicId]);
      }
    },
    [selectedRows, onSelectionChange]
  );

  const toggleAllSelection = useCallback(() => {
    if (selectedRows.length === rows.length) {
      onSelectionChange([]);
    } else {
      onSelectionChange(rows.map((r) => r.public_id));
    }
  }, [selectedRows, rows, onSelectionChange]);

  // Build columns
  const columns = useMemo(() => {
    const cols = [
      // Selection column
      columnHelper.display({
        id: "select",
        header: () => (
          <input
            type="checkbox"
            checked={selectedRows.length === rows.length && rows.length > 0}
            onChange={toggleAllSelection}
            className="h-4 w-4 rounded border-primary"
            aria-label="Select all"
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={isRowSelected(row.original.public_id)}
            onChange={() => toggleRowSelection(row.original.public_id)}
            className="h-4 w-4 rounded border-primary"
            aria-label="Select row"
          />
        ),
        size: 40,
      }),

      // Status column
      columnHelper.accessor("status", {
        header: "Status",
        cell: ({ row }) => {
          const status = row.original.status;
          const errors = row.original.validation_errors || [];
          return (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium",
                      SCRATCHPAD_STATUS_COLORS[status]
                    )}
                  >
                    {status === "INVALID" && (
                      <AlertCircle className="h-3 w-3" />
                    )}
                    {status === "READY" && <Check className="h-3 w-3" />}
                    {SCRATCHPAD_STATUS_LABELS[status]}
                  </span>
                </TooltipTrigger>
                {errors.length > 0 && (
                  <TooltipContent className="max-w-xs">
                    <ul className="text-xs space-y-1">
                      {errors.map((err: ValidationError, idx: number) => (
                        <li key={idx} className="text-destructive">
                          {err.message}
                        </li>
                      ))}
                    </ul>
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          );
        },
        size: 100,
      }),

      // Date column
      columnHelper.accessor("transaction_date", {
        header: "Date",
        cell: ({ row, getValue }) => {
          const value = getValue();
          const isEditing =
            editingCell?.rowId === row.original.public_id &&
            editingCell?.columnId === "transaction_date";

          if (isEditing) {
            return (
              <Input
                type="date"
                defaultValue={value || ""}
                autoFocus
                onBlur={(e) => {
                  if (e.target.value !== value) {
                    onRowUpdate(row.original.public_id, {
                      transaction_date: e.target.value,
                    });
                  }
                  setEditingCell(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setEditingCell(null);
                }}
                className="h-8 w-32"
              />
            );
          }

          return (
            <span
              className="cursor-pointer hover:bg-muted/50 px-2 py-1 rounded ltr-number"
              onClick={() =>
                setEditingCell({
                  rowId: row.original.public_id,
                  columnId: "transaction_date",
                })
              }
            >
              {value || "-"}
            </span>
          );
        },
        size: 120,
      }),

      // Description column
      columnHelper.accessor("description", {
        header: "Description",
        cell: ({ row, getValue }) => {
          const value = getValue();
          const isEditing =
            editingCell?.rowId === row.original.public_id &&
            editingCell?.columnId === "description";

          if (isEditing) {
            return (
              <Input
                defaultValue={value || ""}
                autoFocus
                onBlur={(e) => {
                  if (e.target.value !== value) {
                    onRowUpdate(row.original.public_id, {
                      description: e.target.value,
                    });
                  }
                  setEditingCell(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setEditingCell(null);
                  if (e.key === "Enter") {
                    e.currentTarget.blur();
                  }
                }}
                className="h-8 w-full"
              />
            );
          }

          return (
            <span
              className="cursor-pointer hover:bg-muted/50 px-2 py-1 rounded block truncate max-w-xs"
              onClick={() =>
                setEditingCell({
                  rowId: row.original.public_id,
                  columnId: "description",
                })
              }
            >
              {value || "-"}
            </span>
          );
        },
        size: 200,
      }),

      // Amount column
      columnHelper.accessor("amount", {
        header: "Amount",
        cell: ({ row, getValue }) => {
          const value = getValue();
          const isEditing =
            editingCell?.rowId === row.original.public_id &&
            editingCell?.columnId === "amount";

          if (isEditing) {
            return (
              <Input
                type="number"
                step="0.01"
                min="0"
                defaultValue={value || ""}
                autoFocus
                onBlur={(e) => {
                  if (e.target.value !== value) {
                    onRowUpdate(row.original.public_id, {
                      amount: e.target.value || undefined,
                    });
                  }
                  setEditingCell(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setEditingCell(null);
                  if (e.key === "Enter") {
                    e.currentTarget.blur();
                  }
                }}
                className="h-8 w-28"
              />
            );
          }

          return (
            <span
              className="cursor-pointer hover:bg-muted/50 px-2 py-1 rounded ltr-number font-mono"
              onClick={() =>
                setEditingCell({
                  rowId: row.original.public_id,
                  columnId: "amount",
                })
              }
            >
              {value || "-"}
            </span>
          );
        },
        size: 120,
      }),

      // Debit account column
      columnHelper.accessor("debit_account_id", {
        header: "Debit Account",
        cell: ({ row }) => {
          const value = row.original.debit_account_id;
          const code = row.original.debit_account_code;
          const name = row.original.debit_account_name;
          const isEditing =
            editingCell?.rowId === row.original.public_id &&
            editingCell?.columnId === "debit_account_id";

          if (isEditing) {
            return (
              <Select
                defaultValue={value?.toString() || ""}
                onValueChange={(newValue) => {
                  const accountId = newValue ? parseInt(newValue) : null;
                  if (accountId !== value) {
                    onRowUpdate(row.original.public_id, {
                      debit_account_id: accountId,
                    });
                  }
                  setEditingCell(null);
                }}
              >
                <SelectTrigger className="h-8 w-48">
                  <SelectValue placeholder="Select account" />
                </SelectTrigger>
                <SelectContent>
                  {postableAccounts.map((account) => (
                    <SelectItem key={account.id} value={account.id.toString()}>
                      <span className="font-mono ltr-code">{account.code}</span>
                      {" - "}
                      {account.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            );
          }

          return (
            <span
              className="cursor-pointer hover:bg-muted/50 px-2 py-1 rounded block truncate"
              onClick={() =>
                setEditingCell({
                  rowId: row.original.public_id,
                  columnId: "debit_account_id",
                })
              }
            >
              {code ? (
                <>
                  <span className="font-mono ltr-code text-muted-foreground">
                    {code}
                  </span>{" "}
                  {name}
                </>
              ) : (
                "-"
              )}
            </span>
          );
        },
        size: 200,
      }),

      // Credit account column
      columnHelper.accessor("credit_account_id", {
        header: "Credit Account",
        cell: ({ row }) => {
          const value = row.original.credit_account_id;
          const code = row.original.credit_account_code;
          const name = row.original.credit_account_name;
          const isEditing =
            editingCell?.rowId === row.original.public_id &&
            editingCell?.columnId === "credit_account_id";

          if (isEditing) {
            return (
              <Select
                defaultValue={value?.toString() || ""}
                onValueChange={(newValue) => {
                  const accountId = newValue ? parseInt(newValue) : null;
                  if (accountId !== value) {
                    onRowUpdate(row.original.public_id, {
                      credit_account_id: accountId,
                    });
                  }
                  setEditingCell(null);
                }}
              >
                <SelectTrigger className="h-8 w-48">
                  <SelectValue placeholder="Select account" />
                </SelectTrigger>
                <SelectContent>
                  {postableAccounts.map((account) => (
                    <SelectItem key={account.id} value={account.id.toString()}>
                      <span className="font-mono ltr-code">{account.code}</span>
                      {" - "}
                      {account.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            );
          }

          return (
            <span
              className="cursor-pointer hover:bg-muted/50 px-2 py-1 rounded block truncate"
              onClick={() =>
                setEditingCell({
                  rowId: row.original.public_id,
                  columnId: "credit_account_id",
                })
              }
            >
              {code ? (
                <>
                  <span className="font-mono ltr-code text-muted-foreground">
                    {code}
                  </span>{" "}
                  {name}
                </>
              ) : (
                "-"
              )}
            </span>
          );
        },
        size: 200,
      }),

      // Actions column
      columnHelper.display({
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <button
            onClick={() => onRowDelete(row.original.public_id)}
            className="p-1 text-muted-foreground hover:text-destructive transition-colors"
            title="Delete row"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        ),
        size: 40,
      }),
    ];

    // Add dynamic dimension columns if available
    if (dimensionSchema?.dimensions) {
      const dimCols = dimensionSchema.dimensions.map((dim) =>
        columnHelper.display({
          id: `dim_${dim.code}`,
          header: isRTL && dim.name_ar ? dim.name_ar : dim.name,
          cell: ({ row }) => {
            const rowDim = row.original.dimensions?.find(
              (d) => d.dimension_id === dim.id
            );
            const valueName = rowDim?.dimension_value_name || "-";
            return <span className="text-sm">{valueName}</span>;
          },
          size: 120,
        })
      );
      // Insert dimension columns before the actions column
      cols.splice(cols.length - 1, 0, ...dimCols);
    }

    return cols;
  }, [
    editingCell,
    postableAccounts,
    onRowUpdate,
    onRowDelete,
    dimensionSchema,
    isRTL,
    selectedRows,
    rows,
    isRowSelected,
    toggleRowSelection,
    toggleAllSelection,
  ]);

  const table = useReactTable({
    data: rows,
    columns,
    state: {
      sorting,
    },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getRowId: (row) => row.public_id,
  });

  const { rows: tableRows } = table.getRowModel();

  // Virtual scrolling
  const rowVirtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 48,
    overscan: 5,
  });

  const virtualRows = rowVirtualizer.getVirtualItems();
  const totalSize = rowVirtualizer.getTotalSize();
  const paddingTop = virtualRows.length > 0 ? virtualRows[0]?.start || 0 : 0;
  const paddingBottom =
    virtualRows.length > 0
      ? totalSize - (virtualRows[virtualRows.length - 1]?.end || 0)
      : 0;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        Loading...
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
        <p>No scratchpad rows yet.</p>
        <p className="text-sm mt-2">Add rows to get started.</p>
      </div>
    );
  }

  return (
    <div
      ref={parentRef}
      className="h-[600px] overflow-auto border rounded-md"
    >
      <table className="w-full">
        <thead className="sticky top-0 bg-card z-10 border-b">
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th
                  key={header.id}
                  className="text-start px-3 py-2 text-sm font-medium text-muted-foreground"
                  style={{ width: header.getSize() }}
                >
                  {header.isPlaceholder ? null : (
                    <div
                      className={cn(
                        "flex items-center gap-1",
                        header.column.getCanSort() &&
                          "cursor-pointer select-none hover:text-foreground"
                      )}
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      {flexRender(
                        header.column.columnDef.header,
                        header.getContext()
                      )}
                      {header.column.getIsSorted() === "asc" && (
                        <ChevronUp className="h-4 w-4" />
                      )}
                      {header.column.getIsSorted() === "desc" && (
                        <ChevronDown className="h-4 w-4" />
                      )}
                    </div>
                  )}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {paddingTop > 0 && (
            <tr>
              <td style={{ height: `${paddingTop}px` }} />
            </tr>
          )}
          {virtualRows.map((virtualRow) => {
            const row = tableRows[virtualRow.index];
            return (
              <tr
                key={row.id}
                className={cn(
                  "border-b hover:bg-muted/30 transition-colors",
                  isRowSelected(row.original.public_id) && "bg-primary/5"
                )}
              >
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className="px-3 py-2 text-sm"
                    style={{ width: cell.column.getSize() }}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            );
          })}
          {paddingBottom > 0 && (
            <tr>
              <td style={{ height: `${paddingBottom}px` }} />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
