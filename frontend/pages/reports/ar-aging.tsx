import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import Link from "next/link";
import { Printer, Search, CheckCircle2, AlertTriangle } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
import { useARAging } from "@/queries/useReports";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import type { AgingBucketEntry } from "@/services/reports.service";

interface FlatAgingRow {
  code: string;
  name: string;
  current: number;
  days_31_60: number;
  days_61_90: number;
  over_90: number;
  total: number;
  oldest_open_date: string | null;
}

function flattenBuckets(buckets: Record<string, AgingBucketEntry[]>): FlatAgingRow[] {
  const map = new Map<string, FlatAgingRow>();

  for (const [bucket, entries] of Object.entries(buckets)) {
    for (const entry of entries) {
      const code = entry.customer_code || "";
      const name = entry.customer_name || "";
      const balance = parseFloat(entry.balance) || 0;

      if (!map.has(code)) {
        map.set(code, {
          code,
          name,
          current: 0,
          days_31_60: 0,
          days_61_90: 0,
          over_90: 0,
          total: 0,
          oldest_open_date: entry.oldest_open_date,
        });
      }

      const row = map.get(code)!;
      row[bucket as keyof Pick<FlatAgingRow, "current" | "days_31_60" | "days_61_90" | "over_90">] = balance;
      row.total += balance;
    }
  }

  return Array.from(map.values()).sort((a, b) => b.total - a.total);
}

export default function ARAgingPage() {
  const router = useRouter();
  const { company } = useAuth();
  const [search, setSearch] = useState("");

  const { data, isLoading } = useARAging();

  const rows = data ? flattenBuckets(data.buckets) : [];

  const filteredRows = rows.filter((r) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return r.code.toLowerCase().includes(s) || r.name.toLowerCase().includes(s);
  });

  const totals = filteredRows.reduce(
    (acc, r) => ({
      current: acc.current + r.current,
      days_31_60: acc.days_31_60 + r.days_31_60,
      days_61_90: acc.days_61_90 + r.days_61_90,
      over_90: acc.over_90 + r.over_90,
      total: acc.total + r.total,
    }),
    { current: 0, days_31_60: 0, days_61_90: 0, over_90: 0, total: 0 }
  );

  const formatCurrency = (amount: number) =>
    new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);

  const formatDate = (date: string | null) => {
    if (!date) return "-";
    return new Date(date).toLocaleDateString(
      router.locale === "ar" ? "ar-SA" : "en-US",
      { year: "numeric", month: "short", day: "numeric" }
    );
  };

  return (
    <AppLayout>
      <div className="space-y-6 print:space-y-0">
        <div className="no-print">
          <PageHeader
            title="AR Aging Report"
            subtitle="Accounts Receivable aging by customer"
            actions={
              <div className="flex items-center gap-3">
                {data && (
                  <div className="flex items-center gap-1.5 text-sm">
                    {data.subledger_tied_out ? (
                      <>
                        <CheckCircle2 className="h-4 w-4 text-green-600" />
                        <span className="text-green-600">Subledger tied out</span>
                      </>
                    ) : (
                      <>
                        <AlertTriangle className="h-4 w-4 text-amber-600" />
                        <span className="text-amber-600">Subledger mismatch</span>
                      </>
                    )}
                  </div>
                )}
                <Button variant="outline" onClick={() => window.print()}>
                  <Printer className="me-2 h-4 w-4" />
                  Print
                </Button>
              </div>
            }
          />
        </div>

        <Card className="no-print">
          <CardContent className="pt-6">
            <div className="flex items-center gap-4">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search by code or name..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="ps-10"
                />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Summary Cards */}
        {data && (
          <div className="grid gap-4 md:grid-cols-5 no-print">
            {[
              { label: "Current (0-30)", value: totals.current, color: "text-green-600" },
              { label: "31-60 Days", value: totals.days_31_60, color: "text-amber-600" },
              { label: "61-90 Days", value: totals.days_61_90, color: "text-orange-600" },
              { label: "Over 90 Days", value: totals.over_90, color: "text-red-600" },
              { label: "Total AR", value: totals.total, color: "text-foreground font-bold" },
            ].map((card) => (
              <Card key={card.label}>
                <CardContent className="pt-4 pb-4">
                  <p className="text-sm text-muted-foreground">{card.label}</p>
                  <p className={cn("text-lg font-semibold mt-1 ltr-number", card.color)}>
                    {formatCurrency(card.value)}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {/* Aging Table */}
        <Card className="print:shadow-none print:border-0">
          <CardContent className="pt-6 print:p-0">
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : filteredRows.length === 0 ? (
              <EmptyState
                icon={<CheckCircle2 className="h-12 w-12" />}
                title="No outstanding receivables"
                description="All customer balances are settled."
              />
            ) : (
              <>
                <div className="text-center mb-6 print:mb-8">
                  <h2 className="text-xl font-bold">{company?.name}</h2>
                  <h3 className="text-lg mt-2">Accounts Receivable Aging Report</h3>
                  <p className="text-muted-foreground mt-1">
                    As of: {data ? formatDate(data.as_of) : formatDate(new Date().toISOString())}
                  </p>
                </div>

                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-24">Code</TableHead>
                        <TableHead>Customer</TableHead>
                        <TableHead className="text-end w-28">Current</TableHead>
                        <TableHead className="text-end w-28">31-60 Days</TableHead>
                        <TableHead className="text-end w-28">61-90 Days</TableHead>
                        <TableHead className="text-end w-28">Over 90</TableHead>
                        <TableHead className="text-end w-32">Total</TableHead>
                        <TableHead className="w-28">Oldest Open</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filteredRows.map((row) => (
                        <TableRow key={row.code}>
                          <TableCell className="font-mono ltr-code">
                            <Link
                              href={`/reports/customer-statement?code=${row.code}`}
                              className="hover:underline hover:text-primary"
                            >
                              {row.code}
                            </Link>
                          </TableCell>
                          <TableCell>
                            <Link
                              href={`/reports/customer-statement?code=${row.code}`}
                              className="hover:underline hover:text-primary"
                            >
                              {row.name}
                            </Link>
                          </TableCell>
                          <TableCell className="text-end ltr-number">
                            {row.current > 0 ? formatCurrency(row.current) : "-"}
                          </TableCell>
                          <TableCell className={cn("text-end ltr-number", row.days_31_60 > 0 && "text-amber-600")}>
                            {row.days_31_60 > 0 ? formatCurrency(row.days_31_60) : "-"}
                          </TableCell>
                          <TableCell className={cn("text-end ltr-number", row.days_61_90 > 0 && "text-orange-600")}>
                            {row.days_61_90 > 0 ? formatCurrency(row.days_61_90) : "-"}
                          </TableCell>
                          <TableCell className={cn("text-end ltr-number", row.over_90 > 0 && "text-red-600")}>
                            {row.over_90 > 0 ? formatCurrency(row.over_90) : "-"}
                          </TableCell>
                          <TableCell className="text-end ltr-number font-medium">
                            {formatCurrency(row.total)}
                          </TableCell>
                          <TableCell className="text-sm">
                            {formatDate(row.oldest_open_date)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                    <TableFooter>
                      <TableRow className="font-bold">
                        <TableCell colSpan={2}>Total</TableCell>
                        <TableCell className="text-end ltr-number">
                          {formatCurrency(totals.current)}
                        </TableCell>
                        <TableCell className={cn("text-end ltr-number", totals.days_31_60 > 0 && "text-amber-600")}>
                          {formatCurrency(totals.days_31_60)}
                        </TableCell>
                        <TableCell className={cn("text-end ltr-number", totals.days_61_90 > 0 && "text-orange-600")}>
                          {formatCurrency(totals.days_61_90)}
                        </TableCell>
                        <TableCell className={cn("text-end ltr-number", totals.over_90 > 0 && "text-red-600")}>
                          {formatCurrency(totals.over_90)}
                        </TableCell>
                        <TableCell className="text-end ltr-number">
                          {formatCurrency(totals.total)}
                        </TableCell>
                        <TableCell />
                      </TableRow>
                    </TableFooter>
                  </Table>
                </div>
              </>
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
