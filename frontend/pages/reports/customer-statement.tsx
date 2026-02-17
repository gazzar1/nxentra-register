import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useQuery } from "@tanstack/react-query";
import { Printer, Search, User, Mail, Phone, MapPin, Download } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Separator } from "@/components/ui/separator";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useBilingualText } from "@/components/common/BilingualText";
import { useCustomers } from "@/queries/useAccounts";
import { reportsService } from "@/services/reports.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import { exportTransactionsCSV } from "@/lib/export";

export default function CustomerStatementPage() {
  const { t } = useTranslation(["common", "reports"]);
  const router = useRouter();
  const getText = useBilingualText();
  const { company } = useAuth();

  const [selectedCustomerCode, setSelectedCustomerCode] = useState<string>("");
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");

  const { data: customers } = useCustomers();

  const { data: statement, isLoading, refetch } = useQuery({
    queryKey: ["customer-statement", selectedCustomerCode, dateFrom, dateTo],
    queryFn: async () => {
      if (!selectedCustomerCode) return null;
      const params: { date_from?: string; date_to?: string } = {};
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const { data } = await reportsService.customerStatement(selectedCustomerCode, params);
      return data;
    },
    enabled: !!selectedCustomerCode,
  });

  const formatCurrency = (amount: string | number) => {
    const num = typeof amount === "string" ? parseFloat(amount) : amount;
    return new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(num);
  };

  const formatDate = (date: string) => {
    return new Date(date).toLocaleDateString(
      router.locale === "ar" ? "ar-SA" : "en-US",
      { year: "numeric", month: "short", day: "numeric" }
    );
  };

  const handlePrint = () => {
    window.print();
  };

  const handleExportCSV = () => {
    if (!statement) return;
    exportTransactionsCSV(
      statement.transactions,
      statement.customer.code,
      `customer-statement-${statement.customer.code}`
    );
  };

  const handleSearch = () => {
    refetch();
  };

  return (
    <AppLayout>
      <div className="space-y-6 print:space-y-0">
        <div className="no-print">
          <PageHeader
            title={t("reports:customerStatement.title", "Customer Statement")}
            subtitle={t("reports:customerStatement.subtitle", "View account activity and balance for a customer")}
            actions={
              <div className="flex gap-2">
                <Button variant="outline" onClick={handleExportCSV} disabled={!statement}>
                  <Download className="me-2 h-4 w-4" />
                  {t("reports:actions.exportCSV", "Export CSV")}
                </Button>
                <Button variant="outline" onClick={handlePrint} disabled={!statement}>
                  <Printer className="me-2 h-4 w-4" />
                  {t("reports:actions.print")}
                </Button>
              </div>
            }
          />
        </div>

        {/* Filter Card */}
        <Card className="no-print">
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-end gap-4">
              <div className="space-y-2 min-w-[200px]">
                <Label>{t("reports:filters.customer", "Customer")}</Label>
                <Select value={selectedCustomerCode} onValueChange={setSelectedCustomerCode}>
                  <SelectTrigger>
                    <SelectValue placeholder={t("reports:filters.selectCustomer", "Select customer")} />
                  </SelectTrigger>
                  <SelectContent>
                    {customers?.map((customer) => (
                      <SelectItem key={customer.code} value={customer.code}>
                        {customer.code} - {customer.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>{t("reports:filters.dateFrom", "Date From")}</Label>
                <Input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="w-40"
                />
              </div>

              <div className="space-y-2">
                <Label>{t("reports:filters.dateTo", "Date To")}</Label>
                <Input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="w-40"
                />
              </div>

              <Button onClick={handleSearch} disabled={!selectedCustomerCode}>
                <Search className="me-2 h-4 w-4" />
                {t("reports:filters.search", "Search")}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Statement Content */}
        {isLoading ? (
          <Card>
            <CardContent className="py-12">
              <LoadingSpinner />
            </CardContent>
          </Card>
        ) : !statement ? (
          <Card>
            <CardContent className="py-12">
              <EmptyState
                title={t("reports:customerStatement.noSelection", "Select a Customer")}
                description={t("reports:customerStatement.noSelectionDescription", "Choose a customer to view their statement")}
              />
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Statement Header */}
            <Card>
              <CardHeader className="text-center print:pb-4">
                <CardTitle className="text-xl">{company?.name}</CardTitle>
                <p className="text-lg font-semibold">
                  {t("reports:customerStatement.title", "Customer Statement")}
                </p>
                <p className="text-sm text-muted-foreground">
                  {dateFrom || dateTo
                    ? `${dateFrom ? formatDate(dateFrom) : "..."} - ${dateTo ? formatDate(dateTo) : "..."}`
                    : t("reports:customerStatement.allTime", "All Time")}
                </p>
              </CardHeader>
              <CardContent>
                {/* Customer Info */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                  <div className="space-y-2">
                    <h3 className="font-semibold flex items-center gap-2">
                      <User className="h-4 w-4" />
                      {t("reports:customerStatement.customerInfo", "Customer Information")}
                    </h3>
                    <div className="text-sm space-y-1">
                      <p className="font-medium">{statement.customer.name}</p>
                      <p className="text-muted-foreground">{statement.customer.code}</p>
                      {statement.customer.email && (
                        <p className="flex items-center gap-2">
                          <Mail className="h-3 w-3" /> {statement.customer.email}
                        </p>
                      )}
                      {statement.customer.phone && (
                        <p className="flex items-center gap-2">
                          <Phone className="h-3 w-3" /> {statement.customer.phone}
                        </p>
                      )}
                      {statement.customer.address && (
                        <p className="flex items-center gap-2">
                          <MapPin className="h-3 w-3" /> {statement.customer.address}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Account Summary */}
                  <div className="space-y-2">
                    <h3 className="font-semibold">{t("reports:customerStatement.accountSummary", "Account Summary")}</h3>
                    <div className="grid grid-cols-2 gap-2 text-sm">
                      <span className="text-muted-foreground">{t("reports:customerStatement.currentBalance", "Current Balance")}:</span>
                      <span className={cn("font-medium ltr-number text-end", parseFloat(statement.balance.balance) > 0 && "text-red-600")}>
                        {formatCurrency(statement.balance.balance)}
                      </span>

                      <span className="text-muted-foreground">{t("reports:customerStatement.totalInvoices", "Total Invoices")}:</span>
                      <span className="ltr-number text-end">{formatCurrency(statement.balance.debit_total)}</span>

                      <span className="text-muted-foreground">{t("reports:customerStatement.totalPayments", "Total Payments")}:</span>
                      <span className="ltr-number text-end">{formatCurrency(statement.balance.credit_total)}</span>

                      <span className="text-muted-foreground">{t("reports:customerStatement.creditLimit", "Credit Limit")}:</span>
                      <span className="ltr-number text-end">
                        {statement.customer.credit_limit ? formatCurrency(statement.customer.credit_limit) : "-"}
                      </span>

                      <span className="text-muted-foreground">{t("reports:customerStatement.paymentTerms", "Payment Terms")}:</span>
                      <span className="text-end">{statement.customer.payment_terms_days} days</span>
                    </div>
                  </div>
                </div>

                <Separator />

                {/* Aging Summary */}
                <div className="my-6">
                  <h3 className="font-semibold mb-3">{t("reports:customerStatement.agingSummary", "Aging Summary")}</h3>
                  <div className="grid grid-cols-5 gap-2 text-sm">
                    <div className="text-center p-3 bg-muted rounded">
                      <p className="text-muted-foreground">{t("reports:aging.current", "Current")}</p>
                      <p className="font-medium ltr-number">{formatCurrency(statement.aging.current)}</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded">
                      <p className="text-muted-foreground">{t("reports:aging.31_60", "31-60 Days")}</p>
                      <p className="font-medium ltr-number">{formatCurrency(statement.aging.days_31_60)}</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded">
                      <p className="text-muted-foreground">{t("reports:aging.61_90", "61-90 Days")}</p>
                      <p className="font-medium ltr-number">{formatCurrency(statement.aging.days_61_90)}</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded">
                      <p className="text-muted-foreground">{t("reports:aging.over_90", "Over 90 Days")}</p>
                      <p className={cn("font-medium ltr-number", parseFloat(statement.aging.over_90) > 0 && "text-red-600")}>
                        {formatCurrency(statement.aging.over_90)}
                      </p>
                    </div>
                    <div className="text-center p-3 bg-primary/10 rounded">
                      <p className="text-muted-foreground">{t("reports:aging.total", "Total")}</p>
                      <p className="font-bold ltr-number">{formatCurrency(statement.aging.total)}</p>
                    </div>
                  </div>
                </div>

                <Separator />

                {/* Open Invoices */}
                {statement.open_invoices.length > 0 && (
                  <div className="my-6">
                    <h3 className="font-semibold mb-3">{t("reports:customerStatement.openInvoices", "Open Invoices")}</h3>
                    <div className="rounded-md border">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>{t("reports:columns.invoiceNumber", "Invoice #")}</TableHead>
                            <TableHead>{t("reports:columns.date", "Date")}</TableHead>
                            <TableHead>{t("reports:columns.dueDate", "Due Date")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.total", "Total")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.paid", "Paid")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.due", "Due")}</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {statement.open_invoices.map((invoice) => {
                            const isOverdue = invoice.due_date && new Date(invoice.due_date) < new Date();
                            return (
                              <TableRow key={invoice.invoice_number}>
                                <TableCell className="font-medium">{invoice.invoice_number}</TableCell>
                                <TableCell>{formatDate(invoice.invoice_date)}</TableCell>
                                <TableCell className={cn(isOverdue && "text-red-600")}>
                                  {invoice.due_date ? formatDate(invoice.due_date) : "-"}
                                </TableCell>
                                <TableCell className="text-right ltr-number">{formatCurrency(invoice.total_amount)}</TableCell>
                                <TableCell className="text-right ltr-number">{formatCurrency(invoice.amount_paid)}</TableCell>
                                <TableCell className="text-right ltr-number font-medium">{formatCurrency(invoice.amount_due)}</TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                )}

                <Separator />

                {/* Transaction History */}
                <div className="mt-6">
                  <h3 className="font-semibold mb-3">{t("reports:customerStatement.transactions", "Transaction History")}</h3>
                  {statement.transactions.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-4">
                      {t("reports:customerStatement.noTransactions", "No transactions found for this period.")}
                    </p>
                  ) : (
                    <div className="rounded-md border">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>{t("reports:columns.date", "Date")}</TableHead>
                            <TableHead>{t("reports:columns.entryNumber", "Entry #")}</TableHead>
                            <TableHead>{t("reports:columns.description", "Description")}</TableHead>
                            <TableHead>{t("reports:columns.reference", "Reference")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.debit", "Debit")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.credit", "Credit")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.balance", "Balance")}</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {statement.transactions.map((txn, idx) => (
                            <TableRow key={idx}>
                              <TableCell>{formatDate(txn.date)}</TableCell>
                              <TableCell className="font-mono text-xs">{txn.entry_number}</TableCell>
                              <TableCell>{txn.description}</TableCell>
                              <TableCell>{txn.reference || "-"}</TableCell>
                              <TableCell className="text-right ltr-number">
                                {parseFloat(txn.debit) > 0 ? formatCurrency(txn.debit) : "-"}
                              </TableCell>
                              <TableCell className="text-right ltr-number">
                                {parseFloat(txn.credit) > 0 ? formatCurrency(txn.credit) : "-"}
                              </TableCell>
                              <TableCell className="text-right ltr-number font-medium">{formatCurrency(txn.balance)}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          </>
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
