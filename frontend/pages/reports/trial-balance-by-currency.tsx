import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useQuery } from "@tanstack/react-query";
import { Printer, Download, RefreshCw } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { reportsService, type TrialBalanceByCurrencyRow } from "@/services/reports.service";
import { cn } from "@/lib/cn";

export default function TrialBalanceByCurrencyPage() {
  const { t } = useTranslation(["common", "reports"]);

  const [asOfDate, setAsOfDate] = useState(
    new Date().toISOString().split("T")[0]
  );

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["trial-balance-by-currency", asOfDate],
    queryFn: () =>
      reportsService
        .trialBalanceByCurrency({ as_of_date: asOfDate })
        .then((r) => r.data),
    enabled: !!asOfDate,
  });

  const formatNumber = (val: string | null | undefined) => {
    if (!val || val === "0" || val === "0.00") return "";
    const num = parseFloat(val);
    if (num === 0) return "";
    return num.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  };

  const handlePrint = () => window.print();

  const handleExport = () => {
    if (!data) return;
    const headers = [
      "Account Code", "Account Name", "Currency", "Foreign Debit", "Foreign Credit",
      "Functional Debit", "Functional Credit", "Functional Balance",
      "Current Rate", "Revalued Balance", "Unrealized G/L",
    ];
    const csvRows = [headers.join(",")];
    for (const row of data.rows) {
      csvRows.push([
        row.account_code,
        `"${row.account_name}"`,
        row.currency,
        row.foreign_debit,
        row.foreign_credit,
        row.functional_debit,
        row.functional_credit,
        row.functional_balance,
        row.current_rate || "",
        row.revalued_balance || "",
        row.unrealized_gain_loss || "",
      ].join(","));
    }
    const blob = new Blob([csvRows.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trial-balance-by-currency-${asOfDate}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Group rows by account for visual grouping
  const groupedRows: { account_code: string; account_name: string; rows: TrialBalanceByCurrencyRow[] }[] = [];
  if (data) {
    let currentGroup: typeof groupedRows[0] | null = null;
    for (const row of data.rows) {
      if (!currentGroup || currentGroup.account_code !== row.account_code) {
        currentGroup = { account_code: row.account_code, account_name: row.account_name, rows: [] };
        groupedRows.push(currentGroup);
      }
      currentGroup.rows.push(row);
    }
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Trial Balance by Currency"
          subtitle="Account balances broken down by currency with FX conversion"
          actions={
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={handlePrint}>
                <Printer className="h-4 w-4 me-2" />
                Print
              </Button>
              <Button variant="outline" size="sm" onClick={handleExport} disabled={!data?.rows.length}>
                <Download className="h-4 w-4 me-2" />
                Export CSV
              </Button>
            </div>
          }
        />

        {/* Date selector */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-end gap-4">
              <div className="space-y-2">
                <Label htmlFor="as-of-date">As of Date</Label>
                <Input
                  id="as-of-date"
                  type="date"
                  value={asOfDate}
                  onChange={(e) => setAsOfDate(e.target.value)}
                  className="w-48"
                />
              </div>
              <Button variant="outline" onClick={() => refetch()}>
                <RefreshCw className="me-2 h-4 w-4" />
                Refresh
              </Button>
              {data && (
                <div className="ms-auto text-sm text-muted-foreground">
                  Functional currency: <span className="font-semibold">{data.functional_currency}</span>
                  {" | "}
                  {data.is_balanced ? (
                    <span className="text-green-600">Balanced</span>
                  ) : (
                    <span className="text-red-600">Out of balance</span>
                  )}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Results */}
        <Card>
          <CardHeader>
            <CardTitle>Balances by Currency</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : !data || data.rows.length === 0 ? (
              <EmptyState
                title="No data"
                description="No posted journal entries found for this date."
              />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Account</TableHead>
                      <TableHead className="text-center">Currency</TableHead>
                      <TableHead className="text-end">Foreign Debit</TableHead>
                      <TableHead className="text-end">Foreign Credit</TableHead>
                      <TableHead className="text-end">
                        Func. Debit ({data.functional_currency})
                      </TableHead>
                      <TableHead className="text-end">
                        Func. Credit ({data.functional_currency})
                      </TableHead>
                      <TableHead className="text-end">Rate</TableHead>
                      <TableHead className="text-end">Revalued</TableHead>
                      <TableHead className="text-end">Unrealized G/L</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {groupedRows.map((group) =>
                      group.rows.map((row, idx) => {
                        const gl = row.unrealized_gain_loss ? parseFloat(row.unrealized_gain_loss) : null;
                        return (
                          <TableRow
                            key={`${row.account_code}-${row.currency}`}
                            className={idx > 0 ? "border-t-0" : ""}
                          >
                            <TableCell className={idx > 0 ? "text-transparent select-none" : ""}>
                              <span className="font-mono text-xs me-2">{row.account_code}</span>
                              <span className="text-muted-foreground">{row.account_name}</span>
                            </TableCell>
                            <TableCell className="text-center">
                              <span className={cn(
                                "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                                row.is_foreign
                                  ? "bg-blue-500/10 text-blue-500"
                                  : "bg-gray-100 text-gray-500"
                              )}>
                                {row.currency}
                              </span>
                            </TableCell>
                            <TableCell className="text-end ltr-number font-mono text-sm">
                              {row.is_foreign ? formatNumber(row.foreign_debit) : ""}
                            </TableCell>
                            <TableCell className="text-end ltr-number font-mono text-sm">
                              {row.is_foreign ? formatNumber(row.foreign_credit) : ""}
                            </TableCell>
                            <TableCell className="text-end ltr-number font-mono text-sm">
                              {formatNumber(row.functional_debit)}
                            </TableCell>
                            <TableCell className="text-end ltr-number font-mono text-sm">
                              {formatNumber(row.functional_credit)}
                            </TableCell>
                            <TableCell className="text-end ltr-number font-mono text-xs text-muted-foreground">
                              {row.current_rate ? parseFloat(row.current_rate).toFixed(4) : ""}
                            </TableCell>
                            <TableCell className="text-end ltr-number font-mono text-sm">
                              {formatNumber(row.revalued_balance)}
                            </TableCell>
                            <TableCell className={cn(
                              "text-end ltr-number font-mono text-sm font-medium",
                              gl && gl > 0 ? "text-green-600" : gl && gl < 0 ? "text-red-600" : ""
                            )}>
                              {gl ? (gl > 0 ? "+" : "") + formatNumber(row.unrealized_gain_loss) : ""}
                            </TableCell>
                          </TableRow>
                        );
                      })
                    )}
                  </TableBody>
                  <TableFooter>
                    <TableRow className="font-bold">
                      <TableCell colSpan={4} className="text-end">
                        Totals ({data.functional_currency})
                      </TableCell>
                      <TableCell className="text-end ltr-number font-mono">
                        {formatNumber(data.total_functional_debit)}
                      </TableCell>
                      <TableCell className="text-end ltr-number font-mono">
                        {formatNumber(data.total_functional_credit)}
                      </TableCell>
                      <TableCell colSpan={3}></TableCell>
                    </TableRow>
                  </TableFooter>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "reports"])),
    },
  };
};
