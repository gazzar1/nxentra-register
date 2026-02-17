/**
 * Export utilities for reports
 */

/**
 * Download data as CSV file
 */
export function downloadCSV(data: Record<string, unknown>[], filename: string, columns?: { key: string; label: string }[]) {
  if (data.length === 0) return;

  // Determine columns from data if not provided
  const cols = columns || Object.keys(data[0]).map((key) => ({ key, label: key }));

  // Build CSV content
  const headers = cols.map((c) => `"${c.label}"`).join(",");
  const rows = data.map((row) =>
    cols
      .map((c) => {
        const value = row[c.key];
        if (value === null || value === undefined) return "";
        if (typeof value === "string") return `"${value.replace(/"/g, '""')}"`;
        return String(value);
      })
      .join(",")
  );

  const csv = [headers, ...rows].join("\n");

  // Download
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${filename}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Print current page (PDF via browser print dialog)
 */
export function printPage() {
  window.print();
}

/**
 * Export trial balance to CSV
 */
export function exportTrialBalanceCSV(
  accounts: Array<{
    code: string;
    name: string;
    debit: string;
    credit: string;
    balance: string;
  }>,
  filename = "trial-balance"
) {
  downloadCSV(
    accounts,
    filename,
    [
      { key: "code", label: "Account Code" },
      { key: "name", label: "Account Name" },
      { key: "debit", label: "Debit" },
      { key: "credit", label: "Credit" },
      { key: "balance", label: "Balance" },
    ]
  );
}

/**
 * Export customer/vendor balances to CSV
 */
export function exportBalancesCSV(
  balances: Array<{
    code: string;
    name: string;
    balance: string;
    debit_total?: string;
    credit_total?: string;
  }>,
  type: "customer" | "vendor",
  filename?: string
) {
  const columns = [
    { key: "code", label: `${type === "customer" ? "Customer" : "Vendor"} Code` },
    { key: "name", label: "Name" },
    { key: "balance", label: "Balance" },
    { key: "debit_total", label: "Total Debits" },
    { key: "credit_total", label: "Total Credits" },
  ];

  downloadCSV(balances, filename || `${type}-balances`, columns);
}

/**
 * Export aging report to CSV
 */
export function exportAgingCSV(
  data: Array<{
    code: string;
    name: string;
    current: string;
    days_31_60: string;
    days_61_90: string;
    over_90: string;
    total: string;
  }>,
  type: "ar" | "ap",
  filename?: string
) {
  const columns = [
    { key: "code", label: `${type === "ar" ? "Customer" : "Vendor"} Code` },
    { key: "name", label: "Name" },
    { key: "current", label: "Current (0-30 Days)" },
    { key: "days_31_60", label: "31-60 Days" },
    { key: "days_61_90", label: "61-90 Days" },
    { key: "over_90", label: "Over 90 Days" },
    { key: "total", label: "Total" },
  ];

  downloadCSV(data, filename || `${type}-aging`, columns);
}

/**
 * Export journal entries to CSV
 */
export function exportJournalEntriesCSV(
  entries: Array<{
    entry_number: string;
    date: string;
    description: string;
    reference: string;
    status: string;
    total_debit: string;
    total_credit: string;
  }>,
  filename = "journal-entries"
) {
  downloadCSV(
    entries,
    filename,
    [
      { key: "entry_number", label: "Entry #" },
      { key: "date", label: "Date" },
      { key: "description", label: "Description" },
      { key: "reference", label: "Reference" },
      { key: "status", label: "Status" },
      { key: "total_debit", label: "Debit" },
      { key: "total_credit", label: "Credit" },
    ]
  );
}

/**
 * Export statement transactions to CSV
 */
export function exportTransactionsCSV(
  transactions: Array<{
    date: string;
    entry_number: string;
    description: string;
    reference: string;
    debit: string;
    credit: string;
    balance: string;
  }>,
  entityName: string,
  filename?: string
) {
  downloadCSV(
    transactions,
    filename || `${entityName}-transactions`,
    [
      { key: "date", label: "Date" },
      { key: "entry_number", label: "Entry #" },
      { key: "description", label: "Description" },
      { key: "reference", label: "Reference" },
      { key: "debit", label: "Debit" },
      { key: "credit", label: "Credit" },
      { key: "balance", label: "Balance" },
    ]
  );
}
