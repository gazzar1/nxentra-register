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
 * Export income statement to CSV
 */
export function exportIncomeStatementCSV(
  data: {
    revenue: { accounts: Array<{ code: string; name: string; amount: string; is_header: boolean }> };
    expenses: { accounts: Array<{ code: string; name: string; amount: string; is_header: boolean }> };
    total_revenue: string;
    total_expenses: string;
    net_income: string;
  },
  filename = "income-statement"
) {
  const rows: Record<string, unknown>[] = [];

  // Revenue section
  rows.push({ code: "", name: "REVENUE", amount: "" });
  for (const a of data.revenue.accounts) {
    if (!a.is_header) rows.push({ code: a.code, name: a.name, amount: a.amount });
  }
  rows.push({ code: "", name: "Total Revenue", amount: data.total_revenue });
  rows.push({ code: "", name: "", amount: "" });

  // Expenses section
  rows.push({ code: "", name: "EXPENSES", amount: "" });
  for (const a of data.expenses.accounts) {
    if (!a.is_header) rows.push({ code: a.code, name: a.name, amount: a.amount });
  }
  rows.push({ code: "", name: "Total Expenses", amount: data.total_expenses });
  rows.push({ code: "", name: "", amount: "" });

  // Net income
  rows.push({ code: "", name: "Net Income", amount: data.net_income });

  downloadCSV(rows, filename, [
    { key: "code", label: "Account Code" },
    { key: "name", label: "Account Name" },
    { key: "amount", label: "Amount" },
  ]);
}

/**
 * Export balance sheet to CSV
 */
export function exportBalanceSheetCSV(
  data: {
    assets: { accounts: Array<{ code: string; name: string; balance: string; is_header: boolean }> };
    liabilities: { accounts: Array<{ code: string; name: string; balance: string; is_header: boolean }> };
    equity: { accounts: Array<{ code: string; name: string; balance: string; is_header: boolean }> };
    total_assets: string;
    total_liabilities: string;
    total_equity: string;
    total_liabilities_and_equity: string;
  },
  filename = "balance-sheet"
) {
  const rows: Record<string, unknown>[] = [];

  const addSection = (
    label: string,
    accounts: Array<{ code: string; name: string; balance: string; is_header: boolean }>,
    total: string,
    totalLabel: string
  ) => {
    rows.push({ code: "", name: label, balance: "" });
    for (const a of accounts) {
      if (!a.is_header) rows.push({ code: a.code, name: a.name, balance: a.balance });
    }
    rows.push({ code: "", name: totalLabel, balance: total });
    rows.push({ code: "", name: "", balance: "" });
  };

  addSection("ASSETS", data.assets.accounts, data.total_assets, "Total Assets");
  addSection("LIABILITIES", data.liabilities.accounts, data.total_liabilities, "Total Liabilities");
  addSection("EQUITY", data.equity.accounts, data.total_equity, "Total Equity");

  rows.push({ code: "", name: "Total Liabilities & Equity", balance: data.total_liabilities_and_equity });

  downloadCSV(rows, filename, [
    { key: "code", label: "Account Code" },
    { key: "name", label: "Account Name" },
    { key: "balance", label: "Balance" },
  ]);
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
