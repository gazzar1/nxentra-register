import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { Printer, ArrowUpRight, ArrowDownLeft, Calculator } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { useTaxSummary } from "@/queries/useReports";
import { cn } from "@/lib/cn";

const formatNumber = (value: string | number) => {
  return parseFloat(String(value)).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
};

const formatRate = (rate: string) => {
  const pct = parseFloat(rate) * 100;
  return pct.toFixed(pct % 1 === 0 ? 0 : 1) + "%";
};

export default function TaxSummaryPage() {
  const today = new Date().toISOString().slice(0, 10);
  const firstOfMonth = today.slice(0, 8) + "01";

  const [dateFrom, setDateFrom] = useState(firstOfMonth);
  const [dateTo, setDateTo] = useState(today);

  const { data, isLoading } = useTaxSummary(dateFrom, dateTo);

  if (isLoading) {
    return (
      <AppLayout>
        <LoadingSpinner />
      </AppLayout>
    );
  }

  const outputTax = data?.output_tax;
  const inputTax = data?.input_tax;
  const netTax = data ? parseFloat(data.net_tax) : 0;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Tax Summary"
          subtitle="Output tax (sales) vs. input tax (purchases) for VAT/GST filing"
          actions={
            <Button
              variant="outline"
              size="sm"
              className="no-print"
              onClick={() => window.print()}
            >
              <Printer className="mr-2 h-4 w-4" />
              Print
            </Button>
          }
        />

        {/* Date Filters */}
        <div className="no-print flex items-end gap-4">
          <div>
            <Label htmlFor="date_from">From</Label>
            <Input
              id="date_from"
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="w-44"
            />
          </div>
          <div>
            <Label htmlFor="date_to">To</Label>
            <Input
              id="date_to"
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="w-44"
            />
          </div>
        </div>

        {/* Summary Cards */}
        {data && (
          <div className="grid gap-4 md:grid-cols-4">
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                  <ArrowUpRight className="h-4 w-4 text-red-500" />
                  Output Tax (Sales)
                </div>
                <p className="text-2xl font-bold font-mono">
                  {formatNumber(outputTax?.tax_total || "0")}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  On {formatNumber(outputTax?.taxable_total || "0")} taxable
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                  <ArrowDownLeft className="h-4 w-4 text-green-500" />
                  Input Tax (Purchases)
                </div>
                <p className="text-2xl font-bold font-mono">
                  {formatNumber(inputTax?.recoverable_total || "0")}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Recoverable of {formatNumber(inputTax?.tax_total || "0")} total
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                  Non-Recoverable Tax
                </div>
                <p className="text-2xl font-bold font-mono text-orange-600">
                  {formatNumber(inputTax?.non_recoverable_total || "0")}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Capitalized to cost
                </p>
              </CardContent>
            </Card>
            <Card className={cn(netTax > 0 ? "border-red-200" : "border-green-200")}>
              <CardContent className="pt-6">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                  <Calculator className="h-4 w-4" />
                  Net Tax {netTax > 0 ? "Payable" : "Refundable"}
                </div>
                <p className={cn(
                  "text-2xl font-bold font-mono",
                  netTax > 0 ? "text-red-600" : "text-green-600"
                )}>
                  {formatNumber(Math.abs(netTax))}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Output - Recoverable Input
                </p>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Output Tax Table */}
        <div>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <ArrowUpRight className="h-5 w-5 text-red-500" />
            Output Tax (Sales)
          </h2>
          {outputTax && outputTax.rows.length > 0 ? (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tax Code</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="text-right">Rate</TableHead>
                    <TableHead>GL Account</TableHead>
                    <TableHead className="text-right">Invoices</TableHead>
                    <TableHead className="text-right">Taxable Amount</TableHead>
                    <TableHead className="text-right">Tax Amount</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {outputTax.rows.map((row) => (
                    <TableRow key={row.tax_code}>
                      <TableCell className="font-mono font-medium">{row.tax_code}</TableCell>
                      <TableCell>{row.tax_name}</TableCell>
                      <TableCell>
                        <span className={cn(
                          "inline-block px-2 py-0.5 rounded text-xs font-medium",
                          row.source === "shopify"
                            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                            : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                        )}>
                          {row.source === "shopify" ? "Shopify" : "Invoice"}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono">{formatRate(row.rate)}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        <span className="font-mono">{row.tax_account_code}</span>{" "}
                        {row.tax_account_name}
                      </TableCell>
                      <TableCell className="text-right">{row.invoice_count}</TableCell>
                      <TableCell className="text-right font-mono">{formatNumber(row.taxable_amount)}</TableCell>
                      <TableCell className="text-right font-mono font-medium">{formatNumber(row.tax_amount)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
                <TableFooter>
                  <TableRow>
                    <TableCell colSpan={6} className="font-semibold">Total Output Tax</TableCell>
                    <TableCell className="text-right font-mono font-semibold">
                      {formatNumber(outputTax.taxable_total)}
                    </TableCell>
                    <TableCell className="text-right font-mono font-semibold">
                      {formatNumber(outputTax.tax_total)}
                    </TableCell>
                  </TableRow>
                </TableFooter>
              </Table>
            </Card>
          ) : (
            <EmptyState title="No output tax for this period" />
          )}
        </div>

        {/* Input Tax Table */}
        <div>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <ArrowDownLeft className="h-5 w-5 text-green-500" />
            Input Tax (Purchases)
          </h2>
          {inputTax && inputTax.rows.length > 0 ? (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tax Code</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead className="text-right">Rate</TableHead>
                    <TableHead>GL Account</TableHead>
                    <TableHead className="text-center">Recoverable</TableHead>
                    <TableHead className="text-right">Bills</TableHead>
                    <TableHead className="text-right">Taxable Amount</TableHead>
                    <TableHead className="text-right">Tax Amount</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {inputTax.rows.map((row) => (
                    <TableRow key={row.tax_code}>
                      <TableCell className="font-mono font-medium">{row.tax_code}</TableCell>
                      <TableCell>{row.tax_name}</TableCell>
                      <TableCell className="text-right font-mono">{formatRate(row.rate)}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        <span className="font-mono">{row.tax_account_code}</span>{" "}
                        {row.tax_account_name}
                      </TableCell>
                      <TableCell className="text-center">
                        <span className={cn(
                          "inline-block px-2 py-0.5 rounded text-xs font-medium",
                          row.recoverable
                            ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                            : "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400"
                        )}>
                          {row.recoverable ? "Yes" : "No"}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">{row.bill_count}</TableCell>
                      <TableCell className="text-right font-mono">{formatNumber(row.taxable_amount)}</TableCell>
                      <TableCell className="text-right font-mono font-medium">{formatNumber(row.tax_amount)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
                <TableFooter>
                  <TableRow>
                    <TableCell colSpan={6} className="font-semibold">Total Input Tax</TableCell>
                    <TableCell className="text-right font-mono font-semibold">
                      {formatNumber(inputTax.taxable_total)}
                    </TableCell>
                    <TableCell className="text-right font-mono font-semibold">
                      {formatNumber(inputTax.tax_total)}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell colSpan={6} className="text-muted-foreground">Recoverable</TableCell>
                    <TableCell />
                    <TableCell className="text-right font-mono text-green-600">
                      {formatNumber(inputTax.recoverable_total)}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell colSpan={6} className="text-muted-foreground">Non-Recoverable (capitalized)</TableCell>
                    <TableCell />
                    <TableCell className="text-right font-mono text-orange-600">
                      {formatNumber(inputTax.non_recoverable_total)}
                    </TableCell>
                  </TableRow>
                </TableFooter>
              </Table>
            </Card>
          ) : (
            <EmptyState title="No input tax for this period" />
          )}
        </div>

        {/* Net Tax Position */}
        {data && (
          <Card className={cn(
            "border-2",
            netTax > 0 ? "border-red-200 dark:border-red-900" : "border-green-200 dark:border-green-900"
          )}>
            <CardContent className="pt-6">
              <div className="flex justify-between items-center">
                <div>
                  <h2 className="text-lg font-semibold">Net Tax Position</h2>
                  <p className="text-sm text-muted-foreground">
                    {data.date_from} to {data.date_to}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-sm text-muted-foreground mb-1">
                    {netTax > 0 ? "Amount Payable to Tax Authority" : "Amount Refundable from Tax Authority"}
                  </p>
                  <p className={cn(
                    "text-3xl font-bold font-mono",
                    netTax > 0 ? "text-red-600" : "text-green-600"
                  )}>
                    {formatNumber(Math.abs(netTax))}
                  </p>
                </div>
              </div>
              <div className="mt-4 pt-4 border-t grid grid-cols-3 gap-4 text-sm">
                <div>
                  <span className="text-muted-foreground">Output Tax</span>
                  <p className="font-mono font-medium">{formatNumber(outputTax?.tax_total || "0")}</p>
                </div>
                <div>
                  <span className="text-muted-foreground">Less: Recoverable Input Tax</span>
                  <p className="font-mono font-medium text-green-600">
                    ({formatNumber(inputTax?.recoverable_total || "0")})
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground">Net {netTax > 0 ? "Payable" : "Refundable"}</span>
                  <p className={cn(
                    "font-mono font-bold",
                    netTax > 0 ? "text-red-600" : "text-green-600"
                  )}>
                    {formatNumber(Math.abs(netTax))}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        )}
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
