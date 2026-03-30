import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import { Printer, Search, UserCircle } from "lucide-react";
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
import { useBilingualText } from "@/components/common/BilingualText";
import { useCustomerBalances } from "@/queries/useAccounts";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

export default function CustomerBalancesPage() {
  const { t } = useTranslation(["common", "reports"]);
  const router = useRouter();
  const getText = useBilingualText();
  const { company } = useAuth();
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();
  const [search, setSearch] = useState("");

  const { data, isLoading } = useCustomerBalances();

  const filteredBalances = data?.balances?.filter((b) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      b.customer_code.toLowerCase().includes(searchLower) ||
      b.customer_name.toLowerCase().includes(searchLower) ||
      b.customer_name_ar?.toLowerCase().includes(searchLower)
    );
  });

  const handlePrint = () => {
    window.print();
  };

  // Calculate totals from filtered balances
  const totals = filteredBalances?.reduce(
    (acc, b) => ({
      balance: acc.balance + parseFloat(b.balance),
      debit_total: acc.debit_total + parseFloat(b.debit_total),
      credit_total: acc.credit_total + parseFloat(b.credit_total),
    }),
    { balance: 0, debit_total: 0, credit_total: 0 }
  ) || { balance: 0, debit_total: 0, credit_total: 0 };

  return (
    <AppLayout>
      <div className="space-y-6 print:space-y-0">
        <div className="no-print">
          <PageHeader
            title="Customer Balances"
            subtitle="Accounts Receivable subledger balances"
            actions={
              <Button variant="outline" onClick={handlePrint}>
                <Printer className="me-2 h-4 w-4" />
                Print
              </Button>
            }
          />
        </div>

        {/* Search Filter */}
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

        {/* Balances Table */}
        <Card className="print:shadow-none print:border-0">
          <CardContent className="pt-6 print:p-0">
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : !filteredBalances || filteredBalances.length === 0 ? (
              <EmptyState
                icon={<UserCircle className="h-12 w-12" />}
                title="No customer balances"
                description="No customer balance data available."
              />
            ) : (
              <>
                {/* Report Header */}
                <div className="text-center mb-6 print:mb-8">
                  <h2 className="text-xl font-bold">{company?.name}</h2>
                  <h3 className="text-lg mt-2">Customer Balances (AR Subledger)</h3>
                  <p className="text-muted-foreground mt-1">
                    As of: {formatDate(new Date().toISOString())}
                  </p>
                </div>

                {/* Table */}
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-24">Code</TableHead>
                        <TableHead>Customer Name</TableHead>
                        <TableHead className="text-end w-32">Debit Total</TableHead>
                        <TableHead className="text-end w-32">Credit Total</TableHead>
                        <TableHead className="text-end w-32">Balance</TableHead>
                        <TableHead className="text-center w-20">Txn Count</TableHead>
                        <TableHead className="w-28">Last Invoice</TableHead>
                        <TableHead className="w-28">Last Payment</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filteredBalances.map((balance) => {
                        const balanceValue = parseFloat(balance.balance);
                        return (
                          <TableRow key={balance.customer_code}>
                            <TableCell className="font-mono ltr-code">
                              <Link
                                href={`/accounting/customers/${balance.customer_code}`}
                                className="hover:underline hover:text-primary"
                              >
                                {balance.customer_code}
                              </Link>
                            </TableCell>
                            <TableCell>
                              <Link
                                href={`/accounting/customers/${balance.customer_code}`}
                                className="hover:underline hover:text-primary"
                              >
                                {getText(balance.customer_name, balance.customer_name_ar ?? undefined)}
                              </Link>
                            </TableCell>
                            <TableCell className="text-end ltr-number">
                              {formatCurrency(balance.debit_total)}
                            </TableCell>
                            <TableCell className="text-end ltr-number">
                              {formatCurrency(balance.credit_total)}
                            </TableCell>
                            <TableCell
                              className={cn(
                                "text-end ltr-number font-medium",
                                balanceValue > 0
                                  ? "text-green-600"
                                  : balanceValue < 0
                                  ? "text-red-600"
                                  : ""
                              )}
                            >
                              {formatCurrency(balance.balance)}
                            </TableCell>
                            <TableCell className="text-center">
                              {balance.transaction_count}
                            </TableCell>
                            <TableCell className="text-sm">
                              {balance.last_invoice_date ? formatDate(balance.last_invoice_date) : "-"}
                            </TableCell>
                            <TableCell className="text-sm">
                              {balance.last_payment_date ? formatDate(balance.last_payment_date) : "-"}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                    <TableFooter>
                      <TableRow className="font-bold">
                        <TableCell colSpan={2}>Total</TableCell>
                        <TableCell className="text-end ltr-number">
                          {formatCurrency(totals.debit_total)}
                        </TableCell>
                        <TableCell className="text-end ltr-number">
                          {formatCurrency(totals.credit_total)}
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-end ltr-number",
                            totals.balance > 0
                              ? "text-green-600"
                              : totals.balance < 0
                              ? "text-red-600"
                              : ""
                          )}
                        >
                          {formatCurrency(totals.balance)}
                        </TableCell>
                        <TableCell colSpan={3}></TableCell>
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
